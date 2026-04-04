"""Event Processor - Converts device events into inventory state (CanTracking)."""

import logging
from datetime import datetime
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.can_tracking import CanTracking
from app.models.event import DeviceEvent
from app.models.product import Product
from app.models.inventory import InventoryAdjustment

logger = logging.getLogger("smartlocker.event_processor")


async def process_inventory_events(
    db: AsyncSession,
    device_id: str,
    events: list[DeviceEvent],
) -> int:
    """
    Process a list of DeviceEvent objects and update CanTracking records.

    Called after events are ingested in the sync_events endpoint.
    Returns the number of can tracking records updated.
    """
    updated = 0

    for event in events:
        event_type = event.event_type
        tag_id = event.tag_id
        data = event.data or {}

        if not tag_id:
            # Events without a tag_id cannot update can tracking
            continue

        try:
            if event_type == "can_placed":
                can = await _get_or_create_can(db, tag_id, device_id, data)
                can.status = "in_stock"
                can.slot_id = event.slot_id
                can.placed_at = event.timestamp
                can.last_seen_at = event.timestamp
                if data.get("weight_g"):
                    can.weight_current_g = data["weight_g"]
                    if not can.weight_full_g:
                        can.weight_full_g = data["weight_g"]
                # Resolve product_id: direct or by ppg_code lookup
                product_id = data.get("product_id") or ""
                if not product_id and data.get("ppg_code"):
                    product_id = await _resolve_product_id(
                        db, data["ppg_code"]
                    )
                # Validate product_id exists before setting FK
                if product_id and not can.product_id:
                    if await _product_exists(db, product_id):
                        can.product_id = product_id
                    else:
                        logger.warning(f"Invalid product_id '{product_id}' for tag {tag_id}, skipping FK")

                # Create InventoryAdjustment for barcode-scanned loads
                # so they show up in cloud inventory
                source = data.get("source", "")
                if source in ("barcode_scan", "stock_loading") and product_id:
                    if await _product_exists(db, product_id):
                        weight_g = abs(data.get("weight_g", 0))
                        product = await db.get(Product, product_id)
                        density = (product.density_g_per_ml if product else 1.0) or 1.0
                        liters = (weight_g / density) / 1000 if weight_g > 0 else 0
                        source_label = "Stock loading" if source == "stock_loading" else "Barcode scan"
                        db.add(InventoryAdjustment(
                            device_id=device_id,
                            product_id=product_id,
                            adjustment_type="manual_add",
                            quantity_liters=round(liters, 3),
                            weight_g=weight_g,
                            notes=f"{source_label}: {data.get('ppg_code', '')} batch={data.get('batch_number', '')}",
                            created_by=source,
                        ))
                        logger.info(
                            f"InventoryAdjustment created: +{liters:.2f}L "
                            f"for product {data.get('product_name', product_id)}"
                        )

                updated += 1

            elif event_type == "can_removed":
                can = await _find_can(db, tag_id, device_id)
                if can:
                    can.status = "in_use"
                    can.removed_at = event.timestamp
                    can.last_seen_at = event.timestamp
                    if data.get("weight_g"):
                        can.weight_current_g = data["weight_g"]
                    updated += 1

                # Also create InventoryAdjustment for barcode unloads
                rm_source = data.get("source", "")
                if rm_source in ("barcode_scan", "stock_loading"):
                    rm_product_id = data.get("product_id") or ""
                    if not rm_product_id and data.get("ppg_code"):
                        rm_product_id = await _resolve_product_id(db, data["ppg_code"])
                    if rm_product_id and await _product_exists(db, rm_product_id):
                        weight_g = abs(data.get("weight_g", 0))
                        product = await db.get(Product, rm_product_id)
                        density = (product.density_g_per_ml if product else 1.0) or 1.0
                        liters = (weight_g / density) / 1000 if weight_g > 0 else 0
                        rm_label = "Stock loading" if rm_source == "stock_loading" else "Barcode scan"
                        db.add(InventoryAdjustment(
                            device_id=device_id,
                            product_id=rm_product_id,
                            adjustment_type="manual_remove",
                            quantity_liters=round(liters, 3),
                            weight_g=weight_g,
                            notes=f"{rm_label} unload: {data.get('ppg_code', '')}",
                            created_by=rm_source,
                        ))
                        logger.info(
                            f"InventoryAdjustment created: -{liters:.2f}L "
                            f"for product {data.get('product_name', rm_product_id)}"
                        )

            elif event_type == "can_returned":
                can = await _find_can(db, tag_id, device_id)
                if can:
                    can.status = "in_stock"
                    can.slot_id = event.slot_id
                    can.placed_at = event.timestamp
                    can.last_seen_at = event.timestamp
                    can.times_used += 1
                    # Calculate consumption from weight difference
                    weight_at_removal = data.get("weight_at_removal_g")
                    weight_at_return = data.get("weight_at_return_g")
                    if weight_at_removal and weight_at_return:
                        consumed = weight_at_removal - weight_at_return
                        if consumed > 0:
                            can.total_consumed_g += consumed
                            can.weight_current_g = weight_at_return
                    elif data.get("weight_g"):
                        can.weight_current_g = data["weight_g"]
                    updated += 1

            elif event_type == "can_consumed":
                can = await _find_can(db, tag_id, device_id)
                if can:
                    can.status = "consumed"
                    can.last_seen_at = event.timestamp
                    updated += 1

            elif event_type == "unauthorized_removal":
                can = await _find_can(db, tag_id, device_id)
                if can:
                    can.status = "removed"
                    can.removed_at = event.timestamp
                    can.last_seen_at = event.timestamp
                    updated += 1

        except Exception as e:
            logger.error(f"Error processing event {event_type} for tag {tag_id}: {e}")
            continue

    if updated > 0:
        logger.info(f"Processed {updated} inventory updates for device {device_id}")

    return updated


async def _get_or_create_can(
    db: AsyncSession,
    tag_uid: str,
    device_id: str,
    data: dict,
) -> CanTracking:
    """Find an existing can tracking record or create a new one."""
    can = await _find_can(db, tag_uid, device_id)
    if can:
        return can

    # Create new can tracking record
    can = CanTracking(
        tag_uid=tag_uid,
        device_id=device_id,
        product_id=data.get("product_id"),
        lot_number=data.get("lot_number"),
        can_size_ml=data.get("can_size_ml"),
        weight_tare_g=data.get("weight_tare_g"),
        first_seen_at=datetime.utcnow(),
    )
    db.add(can)
    await db.flush()
    logger.info(f"Created new can tracking record for tag {tag_uid} on device {device_id}")
    return can


async def _find_can(
    db: AsyncSession,
    tag_uid: str,
    device_id: str,
) -> CanTracking | None:
    """Find an existing can tracking record by tag UID and device."""
    result = await db.execute(
        select(CanTracking).where(
            and_(
                CanTracking.tag_uid == tag_uid,
                CanTracking.device_id == device_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def _product_exists(
    db: AsyncSession,
    product_id: str,
) -> bool:
    """Check if a product_id exists in the Product table (FK validation)."""
    try:
        result = await db.execute(
            select(Product.id).where(Product.id == product_id)
        )
        return result.scalar_one_or_none() is not None
    except Exception:
        return False


async def _resolve_product_id(
    db: AsyncSession,
    ppg_code: str,
) -> str | None:
    """Resolve a PPG code to a product_id by looking up the Product table."""
    result = await db.execute(
        select(Product.id).where(
            func.upper(Product.ppg_code) == ppg_code.upper()
        )
    )
    row = result.scalar_one_or_none()
    if row:
        return str(row)
    return None
