"""
Shared sync processing logic -- used by both HTTP API endpoints and WebSocket handler.

Extracts the core business logic from app/api/events.py so that both
the REST endpoints and the WebSocket handler call the same functions.
"""

import logging
from datetime import datetime
from typing import List, Tuple, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.health_log import SensorHealthLog
from app.models.can_tracking import CanTracking

logger = logging.getLogger("smartlocker.sync_service")


async def process_event_batch(
    db: AsyncSession,
    device: LockerDevice,
    events: list,
) -> Tuple[int, int, List[str]]:
    """
    Process a batch of events from an edge device.

    Handles UUID deduplication and persists new events into DeviceEvent,
    then triggers inventory-state processing for any newly created events.

    Args:
        db: Async database session (caller is responsible for commit).
        device: The authenticated LockerDevice row.
        events: List of dicts (or Pydantic-model-like objects) with event fields.

    Returns:
        (received_count, duplicate_count, list_of_acked_event_ids)
    """
    from app.services.event_processor import process_inventory_events

    received = 0
    duplicates = 0
    acked_ids: List[str] = []
    new_events: List[DeviceEvent] = []

    for event_in in events:
        # Support both dict access and attribute access (Pydantic models)
        if isinstance(event_in, dict):
            event_id = event_in.get("event_id") or event_in.get("event_uuid", "")
            event_type = event_in.get("event_type", "")
            timestamp = event_in.get("timestamp", 0)
            shelf_id = event_in.get("shelf_id") or None
            slot_id = event_in.get("slot_id") or None
            tag_id = event_in.get("tag_id") or None
            session_id = event_in.get("session_id") or None
            user_name = event_in.get("user_name") or None
            data = event_in.get("data")
            confirmation = event_in.get("confirmation", "unconfirmed")
        else:
            event_id = getattr(event_in, "event_id", "") or getattr(event_in, "event_uuid", "")
            event_type = getattr(event_in, "event_type", "")
            timestamp = getattr(event_in, "timestamp", 0)
            shelf_id = getattr(event_in, "shelf_id", None) or None
            slot_id = getattr(event_in, "slot_id", None) or None
            tag_id = getattr(event_in, "tag_id", None) or None
            session_id = getattr(event_in, "session_id", None) or None
            user_name = getattr(event_in, "user_name", None) or None
            data = getattr(event_in, "data", None)
            confirmation = getattr(event_in, "confirmation", "unconfirmed")

        try:
            # Check for duplicate (device_id + event_uuid must be unique)
            existing = await db.execute(
                select(DeviceEvent).where(
                    DeviceEvent.device_id == str(device.id),
                    DeviceEvent.event_uuid == event_id,
                )
            )
            if existing.scalar_one_or_none():
                duplicates += 1
                acked_ids.append(event_id)
                continue

            event = DeviceEvent(
                device_id=str(device.id),
                event_uuid=event_id,
                event_type=event_type,
                timestamp=datetime.utcfromtimestamp(timestamp) if timestamp else datetime.utcnow(),
                shelf_id=shelf_id,
                slot_id=slot_id,
                tag_id=tag_id,
                session_id=session_id,
                user_name=user_name,
                data=data,
                confirmation=confirmation,
                received_at=datetime.utcnow(),
            )
            db.add(event)
            await db.flush()
            new_events.append(event)
            received += 1
            acked_ids.append(event_id)
        except Exception:
            # Skip individual failures, continue with batch
            duplicates += 1
            acked_ids.append(event_id)

    # Process new events into inventory state (CanTracking)
    if new_events:
        try:
            await process_inventory_events(db, str(device.id), new_events)
        except Exception as e:
            logger.error(f"Error processing inventory events: {e}")
            # Don't fail the event ingestion if inventory processing fails

    return received, duplicates, acked_ids


async def process_heartbeat(
    db: AsyncSession,
    device: LockerDevice,
    data: dict,
) -> None:
    """
    Process a heartbeat update from an edge device.

    Updates the device's last_heartbeat, status, software_version,
    OTA update tracking, and extended monitoring fields.

    Args:
        db: Async database session (caller is responsible for commit).
        device: The authenticated LockerDevice row.
        data: Dict with optional keys: software_version, driver_status,
              sensor_health, system_info.
    """
    device.last_heartbeat = datetime.utcnow()
    device.status = "online"

    if data.get("software_version"):
        device.software_version = data["software_version"]

    # Auto-clear stale/completed updates via heartbeat
    if device.pending_update_version and data.get("software_version"):
        sv = data["software_version"]
        if device.update_status not in ("completed", None):
            # Device already at or past the target version -> mark completed
            if sv >= device.pending_update_version:
                device.update_status = "completed"
                device.update_completed_at = datetime.utcnow()
                device.pending_update_version = None
                device.pending_update_branch = None
                device.update_error = None
                logger.info(
                    f"Device {device.id} update confirmed: v{sv}"
                )
        elif device.update_status == "completed":
            # Already completed -- clean up leftover fields
            device.pending_update_version = None
            device.pending_update_branch = None

    # Store extended monitoring data
    if data.get("driver_status") is not None:
        device.driver_status = data["driver_status"]
    if data.get("sensor_health") is not None:
        device.sensor_health = data["sensor_health"]
    if data.get("system_info") is not None:
        device.system_info = data["system_info"]


async def process_mixing_sessions(
    db: AsyncSession,
    device: LockerDevice,
    sessions: list,
) -> Tuple[int, List[str]]:
    """
    Process a batch of mixing sessions from an edge device.

    Deduplicates by session_uuid.  Resolves recipe_id by UUID or name.

    Args:
        db: Async database session (caller is responsible for commit).
        device: The authenticated LockerDevice row.
        sessions: List of dicts with mixing session fields.

    Returns:
        (received_count, list_of_acked_session_ids)
    """
    from app.models.mixing import MixingSessionCloud
    from app.models.product import MixingRecipe

    received = 0
    acked_ids: List[str] = []

    for s in sessions:
        # Support both dict and Pydantic model
        if isinstance(s, dict):
            session_id = s.get("session_id", "")
            recipe_id_str = s.get("recipe_id", "")
            job_id = s.get("job_id", "")
            user_name = s.get("user_name", "")
            started_at = s.get("started_at", 0)
            completed_at = s.get("completed_at", 0)
            base_weight_target_g = s.get("base_weight_target_g", 0)
            base_weight_actual_g = s.get("base_weight_actual_g", 0)
            hardener_weight_target_g = s.get("hardener_weight_target_g", 0)
            hardener_weight_actual_g = s.get("hardener_weight_actual_g", 0)
            thinner_weight_g = s.get("thinner_weight_g", 0)
            ratio_achieved = s.get("ratio_achieved", 0)
            ratio_in_spec = s.get("ratio_in_spec", False)
            application_method = s.get("application_method", "brush")
            status = s.get("status", "completed")
        else:
            session_id = getattr(s, "session_id", "")
            recipe_id_str = getattr(s, "recipe_id", "")
            job_id = getattr(s, "job_id", "")
            user_name = getattr(s, "user_name", "")
            started_at = getattr(s, "started_at", 0)
            completed_at = getattr(s, "completed_at", 0)
            base_weight_target_g = getattr(s, "base_weight_target_g", 0)
            base_weight_actual_g = getattr(s, "base_weight_actual_g", 0)
            hardener_weight_target_g = getattr(s, "hardener_weight_target_g", 0)
            hardener_weight_actual_g = getattr(s, "hardener_weight_actual_g", 0)
            thinner_weight_g = getattr(s, "thinner_weight_g", 0)
            ratio_achieved = getattr(s, "ratio_achieved", 0)
            ratio_in_spec = getattr(s, "ratio_in_spec", False)
            application_method = getattr(s, "application_method", "brush")
            status = getattr(s, "status", "completed")

        # Deduplicate by session_uuid
        existing = await db.execute(
            select(MixingSessionCloud).where(
                MixingSessionCloud.session_uuid == session_id
            )
        )
        if existing.scalar_one_or_none():
            acked_ids.append(session_id)  # Already exists, still ack
            continue

        # Find recipe by recipe_id string (could be name or UUID)
        recipe_id_fk = None
        if recipe_id_str:
            recipe_result = await db.execute(
                select(MixingRecipe).where(
                    (MixingRecipe.id == recipe_id_str) | (MixingRecipe.name == recipe_id_str)
                )
            )
            recipe = recipe_result.scalar_one_or_none()
            if recipe:
                recipe_id_fk = recipe.id

        record = MixingSessionCloud(
            device_id=device.id,
            session_uuid=session_id,
            recipe_id=recipe_id_fk,
            job_id=job_id,
            user_name=user_name,
            started_at=datetime.utcfromtimestamp(started_at) if started_at else None,
            completed_at=datetime.utcfromtimestamp(completed_at) if completed_at else None,
            base_weight_target_g=base_weight_target_g,
            base_weight_actual_g=base_weight_actual_g,
            hardener_weight_target_g=hardener_weight_target_g,
            hardener_weight_actual_g=hardener_weight_actual_g,
            thinner_weight_g=thinner_weight_g,
            ratio_achieved=ratio_achieved,
            ratio_in_spec=ratio_in_spec,
            application_method=application_method,
            status=status,
        )
        db.add(record)
        received += 1
        acked_ids.append(session_id)

    return received, acked_ids


async def process_health_logs(
    db: AsyncSession,
    device: LockerDevice,
    logs: list,
) -> int:
    """
    Process a batch of sensor health logs from an edge device.

    Args:
        db: Async database session (caller is responsible for commit).
        device: The authenticated LockerDevice row.
        logs: List of dicts with keys: timestamp, sensor, status, message, value.

    Returns:
        Number of logs successfully received.
    """
    received = 0
    for log in logs:
        try:
            if isinstance(log, dict):
                ts_str = log.get("timestamp", "")
                sensor = log.get("sensor", "")
                status = log.get("status", "")
                message = log.get("message", "")
                value = log.get("value", "")
            else:
                ts_str = getattr(log, "timestamp", "")
                sensor = getattr(log, "sensor", "")
                status = getattr(log, "status", "")
                message = getattr(log, "message", "") or ""
                value = getattr(log, "value", "") or ""

            # Parse timestamp (ISO format from edge)
            try:
                ts = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                ts = datetime.utcnow()

            entry = SensorHealthLog(
                device_id=device.id,
                timestamp=ts,
                sensor=sensor,
                status=status,
                message=message or '',
                value=value or '',
                received_at=datetime.utcnow(),
            )
            db.add(entry)
            received += 1
        except Exception:
            continue  # Skip individual failures

    return received


async def process_inventory_snapshot(
    db: AsyncSession,
    device: LockerDevice,
    slots: list,
) -> int:
    """
    Process an inventory snapshot (full slot state) from an edge device.

    For each slot with a tag, finds or creates a CanTracking record
    and updates it with the current weight and status.

    Args:
        db: Async database session (caller is responsible for commit).
        device: The authenticated LockerDevice row.
        slots: List of dicts with slot data (slot_id, tag_uid/current_tag_id,
               product_id/current_product_id, weight_g/weight_current_g, status).

    Returns:
        Number of slots processed.
    """
    updated = 0
    for slot_data in slots:
        if isinstance(slot_data, dict):
            tag = slot_data.get("tag_uid") or slot_data.get("current_tag_id")
            prod_id = slot_data.get("product_id") or slot_data.get("current_product_id")
            weight = slot_data.get("weight_g") or slot_data.get("weight_current_g")
            slot_id = slot_data.get("slot_id", "")
            status = slot_data.get("status", "empty")
        else:
            # Pydantic model with resolved_* properties (from SlotState)
            tag = getattr(slot_data, "resolved_tag_uid", None) or getattr(slot_data, "tag_uid", None) or getattr(slot_data, "current_tag_id", None)
            prod_id = getattr(slot_data, "resolved_product_id", None) or getattr(slot_data, "product_id", None) or getattr(slot_data, "current_product_id", None)
            weight = getattr(slot_data, "resolved_weight_g", None) or getattr(slot_data, "weight_g", None) or getattr(slot_data, "weight_current_g", None)
            slot_id = getattr(slot_data, "slot_id", "")
            status = getattr(slot_data, "status", "empty")

        if not tag:
            continue

        # Find or create can tracking record
        can_result = await db.execute(
            select(CanTracking).where(
                and_(
                    CanTracking.tag_uid == tag,
                    CanTracking.device_id == str(device.id),
                )
            )
        )
        can = can_result.scalar_one_or_none()

        if can:
            can.slot_id = slot_id
            can.weight_current_g = weight
            can.last_seen_at = datetime.utcnow()
            if status == "occupied":
                can.status = "in_stock"
            elif status == "in_use":
                can.status = "in_use"
            if prod_id and not can.product_id:
                can.product_id = prod_id
        else:
            can = CanTracking(
                tag_uid=tag,
                device_id=str(device.id),
                product_id=prod_id,
                slot_id=slot_id,
                weight_current_g=weight,
                weight_full_g=weight,
                status="in_stock" if status == "occupied" else status,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                placed_at=datetime.utcnow(),
            )
            db.add(can)

        updated += 1

    return updated
