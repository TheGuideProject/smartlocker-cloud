"""
Command Service — Creates DeviceCommands when admin changes products/recipes/config.
Pushes commands via WebSocket if device is connected, otherwise queues for later delivery.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.device import LockerDevice
from app.models.command import DeviceCommand
from app.models.product import Product, MixingRecipe

logger = logging.getLogger("smartlocker.command_service")


async def create_product_sync_command(db: AsyncSession):
    """
    Create a product_sync command for ALL active paired devices.
    Called when products are added/edited/deleted.
    """
    # Load current active product catalog
    result = await db.execute(
        select(Product).where(Product.is_active == True)
    )
    products = result.scalars().all()

    product_list = []
    for p in products:
        product_list.append({
            "id": p.id,
            "ppg_code": p.ppg_code,
            "name": p.name,
            "product_type": p.product_type,
            "density_g_per_ml": p.density_g_per_ml,
            "pot_life_minutes": p.pot_life_minutes,
            "hazard_class": p.hazard_class or "",
            "can_sizes_ml": p.can_sizes_ml or [],
            "can_tare_weight_g": p.can_tare_weight_g or {},
        })

    payload = {"products": product_list}
    await _create_commands_for_all_devices(db, "product_sync", payload)


async def create_recipe_sync_command(db: AsyncSession):
    """
    Create a recipe_sync command for ALL active paired devices.
    Called when recipes are added/edited/deleted.
    """
    result = await db.execute(
        select(MixingRecipe).where(MixingRecipe.is_active == True)
    )
    recipes = result.scalars().all()

    recipe_list = []
    for r in recipes:
        recipe_list.append({
            "id": r.id,
            "name": r.name,
            "base_product_id": r.base_product_id,
            "hardener_product_id": r.hardener_product_id,
            "ratio_base": r.ratio_base,
            "ratio_hardener": r.ratio_hardener,
            "tolerance_pct": r.tolerance_pct,
            "thinner_pct_brush": r.thinner_pct_brush,
            "thinner_pct_roller": r.thinner_pct_roller,
            "thinner_pct_spray": r.thinner_pct_spray,
            "recommended_thinner_id": r.recommended_thinner_id,
            "pot_life_minutes": r.pot_life_minutes,
        })

    payload = {"recipes": recipe_list}
    await _create_commands_for_all_devices(db, "recipe_sync", payload)


async def _create_commands_for_all_devices(
    db: AsyncSession, command_type: str, payload: dict
):
    """Create a DeviceCommand for every active device and push via WS if connected."""
    # Get all paired devices (all devices should receive sync commands)
    result = await db.execute(select(LockerDevice))
    devices = result.scalars().all()

    if not devices:
        return

    # Import manager lazily to avoid circular imports
    try:
        from app.api.websocket import manager
    except ImportError:
        manager = None

    for device in devices:
        cmd = DeviceCommand(
            device_id=device.id,
            command_type=command_type,
            payload=payload,
            status="pending",
        )
        db.add(cmd)
        await db.flush()  # Get the cmd.id

        # Try to push via WebSocket if device is connected
        if manager and manager.is_connected(device.device_id):
            sent = await manager.send_to_device(device.device_id, {
                "type": "command",
                "command_id": cmd.id,
                "command_type": command_type,
                "payload": payload,
            })
            if sent:
                cmd.status = "delivered"
                cmd.delivered_at = datetime.utcnow()
                logger.info(f"[CMD] {command_type} pushed to {device.device_id} via WS")
            else:
                logger.info(f"[CMD] {command_type} queued for {device.device_id} (WS send failed)")
        else:
            logger.info(f"[CMD] {command_type} queued for {device.device_id} (offline)")

    logger.info(f"[CMD] Created {len(devices)} {command_type} commands")
