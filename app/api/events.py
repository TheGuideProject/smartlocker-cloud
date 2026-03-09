"""Event Ingestion API - Receives batched events from edge devices."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, text, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.health_log import SensorHealthLog
from app.services.event_processor import process_inventory_events

logger = logging.getLogger("smartlocker.events")

router = APIRouter(prefix="/api/devices", tags=["devices"])


# ---- Schemas ----

class EventIn(BaseModel):
    event_id: str
    event_type: str
    timestamp: float
    device_id: str = ""
    shelf_id: str = ""
    slot_id: str = ""
    tag_id: str = ""
    session_id: str = ""
    user_name: str = ""
    data: Optional[dict] = None
    confirmation: str = "unconfirmed"
    sequence_num: int = 0


class EventBatch(BaseModel):
    events: List[EventIn]


class EventAck(BaseModel):
    received: int
    duplicates: int
    event_ids: List[str]


class HeartbeatIn(BaseModel):
    software_version: Optional[str] = None
    uptime_hours: Optional[float] = None
    sync_queue_depth: Optional[int] = None
    # Extended monitoring fields
    driver_status: Optional[dict] = None   # {"rfid": "real", "weight": "fake", ...}
    sensor_health: Optional[dict] = None   # Per-sensor health data
    system_info: Optional[dict] = None     # {"uptime_seconds": ..., "events_pending_sync": ...}


class UpdateStatusIn(BaseModel):
    update_status: str
    software_version: str = ""
    error_message: str = ""


# ---- Device Auth Helper ----

async def verify_device_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> LockerDevice:
    """Verify device API key from header."""
    # For MVP, simple plaintext comparison (use hashed in production)
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.api_key_hash == x_api_key)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return device


# ---- Endpoints ----

@router.post("/{device_id}/events", response_model=EventAck)
async def ingest_events(
    device_id: str,
    batch: EventBatch,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive a batch of events from an edge device.
    Uses INSERT ON CONFLICT DO NOTHING for UUID deduplication.
    """
    # Find device
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not registered")

    received = 0
    duplicates = 0
    acked_ids = []
    new_events = []  # Collect newly created events for inventory processing

    for event_in in batch.events:
        # Check for duplicate (device_id + event_uuid must be unique)
        try:
            existing = await db.execute(
                select(DeviceEvent).where(
                    DeviceEvent.device_id == str(device.id),
                    DeviceEvent.event_uuid == event_in.event_id,
                )
            )
            if existing.scalar_one_or_none():
                duplicates += 1
                acked_ids.append(event_in.event_id)
                continue

            event = DeviceEvent(
                device_id=str(device.id),
                event_uuid=event_in.event_id,
                event_type=event_in.event_type,
                timestamp=datetime.utcfromtimestamp(event_in.timestamp),
                shelf_id=event_in.shelf_id or None,
                slot_id=event_in.slot_id or None,
                tag_id=event_in.tag_id or None,
                session_id=event_in.session_id or None,
                user_name=event_in.user_name or None,
                data=event_in.data,
                confirmation=event_in.confirmation,
                received_at=datetime.utcnow(),
            )
            db.add(event)
            await db.flush()
            new_events.append(event)
            received += 1
            acked_ids.append(event_in.event_id)
        except Exception:
            # Skip individual failures, continue with batch
            duplicates += 1
            acked_ids.append(event_in.event_id)

    # Update device heartbeat
    device.last_heartbeat = datetime.utcnow()
    device.status = "online"

    # Process new events into inventory state (CanTracking)
    if new_events:
        try:
            await process_inventory_events(db, str(device.id), new_events)
        except Exception as e:
            logger.error(f"Error processing inventory events for device {device_id}: {e}")
            # Don't fail the event ingestion if inventory processing fails

    return EventAck(
        received=received,
        duplicates=duplicates,
        event_ids=acked_ids,
    )


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str,
    heartbeat: HeartbeatIn,
    db: AsyncSession = Depends(get_db),
):
    """Update device status."""
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.last_heartbeat = datetime.utcnow()
    device.status = "online"
    if heartbeat.software_version:
        device.software_version = heartbeat.software_version

    # Auto-detect update completion via heartbeat version match
    if (device.pending_update_version
            and heartbeat.software_version
            and heartbeat.software_version == device.pending_update_version
            and device.update_status not in ("completed", None)):
        device.update_status = "completed"
        device.update_completed_at = datetime.utcnow()
        device.pending_update_version = None
        device.pending_update_branch = None
        device.update_error = None
        logger.info(f"Device {device_id} auto-confirmed update to v{heartbeat.software_version}")

    # Store extended monitoring data
    if heartbeat.driver_status is not None:
        device.driver_status = heartbeat.driver_status
    if heartbeat.sensor_health is not None:
        device.sensor_health = heartbeat.sensor_health
    if heartbeat.system_info is not None:
        device.system_info = heartbeat.system_info

    return {"status": "ok"}


# ---- OTA Update Status ----

@router.post("/{device_id}/update-status")
async def report_update_status(
    device_id: str,
    payload: UpdateStatusIn,
    db: AsyncSession = Depends(get_db),
):
    """Receive OTA update progress from edge device."""
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.update_status = payload.update_status

    if payload.update_status == "completed":
        device.update_completed_at = datetime.utcnow()
        device.pending_update_version = None
        device.pending_update_branch = None
        device.update_error = None
        if payload.software_version:
            device.software_version = payload.software_version
        logger.info(f"Device {device_id} update COMPLETED: v{payload.software_version}")

    elif payload.update_status == "failed":
        device.update_error = payload.error_message[:500] if payload.error_message else "Unknown error"
        device.update_completed_at = datetime.utcnow()
        logger.warning(f"Device {device_id} update FAILED: {payload.error_message}")

    await db.commit()
    return {"status": "ok"}


# ---- Health Log Ingestion ----

class HealthLogIn(BaseModel):
    id: Optional[int] = None  # Edge local ID (not stored in cloud)
    timestamp: str
    sensor: str
    status: str
    message: Optional[str] = ""
    value: Optional[str] = ""


class HealthLogBatch(BaseModel):
    logs: List[HealthLogIn]


@router.post("/{device_id}/health-logs")
async def receive_health_logs(
    device_id: str,
    batch: HealthLogBatch,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive a batch of sensor health logs from an edge device.
    Called when edge device comes back online after offline period.
    """
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not registered")

    received = 0
    for log in batch.logs:
        try:
            # Parse timestamp (ISO format from edge)
            try:
                ts = datetime.fromisoformat(log.timestamp.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                ts = datetime.utcnow()

            entry = SensorHealthLog(
                device_id=device.id,
                timestamp=ts,
                sensor=log.sensor,
                status=log.status,
                message=log.message or '',
                value=log.value or '',
                received_at=datetime.utcnow(),
            )
            db.add(entry)
            received += 1
        except Exception:
            continue  # Skip individual failures

    return {"received": received}


# ---- Health Summary (smart aggregation) ----

@router.get("/{device_id}/health-summary")
async def get_health_summary(
    device_id: str,
    hours: int = 48,
    db: AsyncSession = Depends(get_db),
):
    """
    Get smart-aggregated health summary for a device.

    Instead of returning raw logs, this groups consecutive errors into
    periods and returns a concise summary per sensor:
      - If error: "FAILING for 2 days (576 errors since Mar 7)"
      - If ok: "Operating normally (since Mar 8 14:30)"
    """
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    summary = await _aggregate_sensor_issues(db, device.id, hours=hours)
    return {"device_id": device_id, "hours": hours, "sensors": summary}


async def _aggregate_sensor_issues(
    db: AsyncSession, device_id: str, hours: int = 48
) -> list:
    """
    Smart aggregation: group consecutive errors into periods.

    Instead of showing 100 individual errors, produce summaries like:
        "Weight sensor FAILING for 2 days (576 errors since Mar 7)"
        "RFID: Operating normally (since Mar 8 14:30)"
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Get distinct sensors that have logs in the time window
    sensor_result = await db.execute(
        select(SensorHealthLog.sensor).where(
            and_(
                SensorHealthLog.device_id == device_id,
                SensorHealthLog.timestamp >= cutoff,
            )
        ).distinct()
    )
    sensors = [row[0] for row in sensor_result.fetchall()]

    aggregated = []

    for sensor_name in sensors:
        # Get all logs for this sensor, ordered by timestamp DESC
        logs_result = await db.execute(
            select(SensorHealthLog).where(
                and_(
                    SensorHealthLog.device_id == device_id,
                    SensorHealthLog.sensor == sensor_name,
                    SensorHealthLog.timestamp >= cutoff,
                )
            ).order_by(desc(SensorHealthLog.timestamp))
        )
        logs = logs_result.scalars().all()

        if not logs:
            continue

        # Latest log determines current status
        latest = logs[0]
        current_status = latest.status

        if current_status in ('error', 'disconnected', 'warning', 'out_of_range'):
            # Find the start of the current error streak
            # (walk backwards from latest while status is non-ok)
            streak_start = latest.timestamp
            error_count = 0
            last_message = latest.message

            for log in logs:
                if log.status in ('error', 'disconnected', 'warning', 'out_of_range'):
                    streak_start = log.timestamp
                    error_count += 1
                    if not last_message and log.message:
                        last_message = log.message
                else:
                    break  # Hit an OK status, streak ends here

            # Calculate duration
            duration = datetime.utcnow() - streak_start
            duration_hours = round(duration.total_seconds() / 3600, 1)

            # Build human-readable duration
            if duration_hours < 1:
                duration_str = f"{int(duration.total_seconds() / 60)} minutes"
            elif duration_hours < 24:
                duration_str = f"{duration_hours} hours"
            else:
                days = round(duration_hours / 24, 1)
                duration_str = f"{days} days"

            streak_start_str = streak_start.strftime('%b %d %H:%M')
            summary_text = (
                f"{sensor_name.upper()} FAILING for {duration_str} "
                f"({error_count} errors since {streak_start_str})"
            )

            aggregated.append({
                "sensor": sensor_name,
                "current_status": current_status,
                "streak_start": streak_start.isoformat(),
                "streak_duration_hours": duration_hours,
                "error_count": error_count,
                "last_message": last_message,
                "summary": summary_text,
            })
        else:
            # Sensor is OK -- find when it became OK (last error before current OK streak)
            ok_since = latest.timestamp
            for log in logs:
                if log.status == 'ok':
                    ok_since = log.timestamp
                else:
                    break

            ok_since_str = ok_since.strftime('%b %d %H:%M')
            summary_text = f"Operating normally (since {ok_since_str})"

            aggregated.append({
                "sensor": sensor_name,
                "current_status": "ok",
                "ok_since": ok_since.isoformat(),
                "summary": summary_text,
            })

    return aggregated


# ---- Inventory Snapshot from Edge ----

class SlotState(BaseModel):
    slot_id: str
    tag_uid: Optional[str] = None
    product_id: Optional[str] = None
    weight_g: Optional[float] = None
    status: str = "empty"  # empty, occupied, in_use


class InventorySnapshotIn(BaseModel):
    slots: List[SlotState]


@router.post("/{device_id}/inventory-snapshot")
async def receive_inventory_snapshot(
    device_id: str,
    payload: InventorySnapshotIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive current slot state from edge device.

    This is a full snapshot of the device's current inventory,
    used for reconciliation and initial sync.
    """
    from app.models.can_tracking import CanTracking

    result = await db.execute(
        select(LockerDevice).where(LockerDevice.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not registered")

    updated = 0
    for slot in payload.slots:
        if not slot.tag_uid:
            continue

        # Find or create can tracking record
        can_result = await db.execute(
            select(CanTracking).where(
                and_(
                    CanTracking.tag_uid == slot.tag_uid,
                    CanTracking.device_id == str(device.id),
                )
            )
        )
        can = can_result.scalar_one_or_none()

        if can:
            can.slot_id = slot.slot_id
            can.weight_current_g = slot.weight_g
            can.last_seen_at = datetime.utcnow()
            if slot.status == "occupied":
                can.status = "in_stock"
            elif slot.status == "in_use":
                can.status = "in_use"
            if slot.product_id and not can.product_id:
                can.product_id = slot.product_id
        else:
            can = CanTracking(
                tag_uid=slot.tag_uid,
                device_id=str(device.id),
                product_id=slot.product_id,
                slot_id=slot.slot_id,
                weight_current_g=slot.weight_g,
                weight_full_g=slot.weight_g,
                status="in_stock" if slot.status == "occupied" else slot.status,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                placed_at=datetime.utcnow(),
            )
            db.add(can)

        updated += 1

    device.last_heartbeat = datetime.utcnow()
    device.status = "online"

    return {"status": "ok", "slots_processed": updated}
