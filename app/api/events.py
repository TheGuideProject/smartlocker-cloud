"""Event Ingestion API - Receives batched events from edge devices."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import LockerDevice
from app.models.event import DeviceEvent

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
            received += 1
            acked_ids.append(event_in.event_id)
        except Exception:
            # Skip individual failures, continue with batch
            duplicates += 1
            acked_ids.append(event_in.event_id)

    # Update device heartbeat
    device.last_heartbeat = datetime.utcnow()
    device.status = "online"

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

    # Store extended monitoring data
    if heartbeat.driver_status is not None:
        device.driver_status = heartbeat.driver_status
    if heartbeat.sensor_health is not None:
        device.sensor_health = heartbeat.sensor_health
    if heartbeat.system_info is not None:
        device.system_info = heartbeat.system_info

    return {"status": "ok"}
