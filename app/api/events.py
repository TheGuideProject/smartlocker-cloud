"""Event Ingestion API - Receives batched events from edge devices."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from sqlalchemy import select, text, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.health_log import SensorHealthLog
from app.models.support_request import SupportRequest
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
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Receive a batch of events from an edge device.
    Uses INSERT ON CONFLICT DO NOTHING for UUID deduplication.
    """

    try:
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
            except Exception as e:
                logger.error(f"Event insert error for {event_in.event_id}: {e}")
                await db.rollback()
                # Skip individual failures, continue with batch
                duplicates += 1
                acked_ids.append(event_in.event_id)

        # Update device heartbeat
        device.last_heartbeat = datetime.utcnow()
        device.status = "online"

        # Update pending counter in system_info (so cloud dashboard reflects sync immediately)
        if received > 0 and device.system_info:
            try:
                si = dict(device.system_info)
                old_pending = si.get("events_pending_sync", 0)
                si["events_pending_sync"] = max(0, old_pending - received - duplicates)
                device.system_info = si
            except Exception:
                pass  # Non-critical

        # Process new events into inventory state (CanTracking)
        if new_events:
            try:
                await process_inventory_events(db, str(device.id), new_events)
            except Exception as e:
                logger.error(f"Error processing inventory events for device {device_id}: {e}")
                # Don't fail the event ingestion if inventory processing fails

        await db.commit()
        return EventAck(
            received=received,
            duplicates=duplicates,
            event_ids=acked_ids,
        )
    except Exception as e:
        logger.error(f"EVENTS ENDPOINT ERROR for {device_id}: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str,
    heartbeat: HeartbeatIn,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Update device status."""
    try:
        return await _process_heartbeat(device_id, heartbeat, device, db)
    except Exception as e:
        logger.error(f"HEARTBEAT ERROR for {device_id}: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)[:200])


async def _process_heartbeat(device_id, heartbeat, device, db):
    device.last_heartbeat = datetime.utcnow()
    device.status = "online"
    if heartbeat.software_version:
        device.software_version = heartbeat.software_version

    # Auto-clear stale/completed updates via heartbeat
    if device.pending_update_version and heartbeat.software_version:
        if device.update_status not in ("completed", None):
            # Device already at or past the target version → mark completed
            if heartbeat.software_version >= device.pending_update_version:
                device.update_status = "completed"
                device.update_completed_at = datetime.utcnow()
                device.pending_update_version = None
                device.pending_update_branch = None
                device.update_error = None
                logger.info(f"Device {device_id} update confirmed: v{heartbeat.software_version}")
        elif device.update_status == "completed":
            # Already completed — clean up leftover fields
            device.pending_update_version = None
            device.pending_update_branch = None

    # Store extended monitoring data
    if heartbeat.driver_status is not None:
        device.driver_status = heartbeat.driver_status
    if heartbeat.sensor_health is not None:
        device.sensor_health = heartbeat.sensor_health
    if heartbeat.system_info is not None:
        device.system_info = heartbeat.system_info

    # Deliver pending commands via heartbeat response (faster than config polling)
    # This makes OTA/restart/reboot commands arrive within ~60s instead of ~120s
    pending_commands = []
    try:
        from app.models.command import DeviceCommand
        pending_cmds_result = await db.execute(
            select(DeviceCommand)
            .where(
                DeviceCommand.device_id == device.id,
                DeviceCommand.status == "pending",
            )
            .order_by(DeviceCommand.created_at)
            .limit(10)
        )
        pending_cmds = pending_cmds_result.scalars().all()
        if pending_cmds:
            for cmd in pending_cmds:
                pending_commands.append({
                    "command_id": cmd.id,
                    "command_type": cmd.command_type,
                    "payload": cmd.payload or {},
                })
                cmd.status = "delivered"
                cmd.delivered_at = datetime.utcnow()
            logger.info(f"Delivered {len(pending_cmds)} commands via heartbeat to {device_id}")
    except Exception as e:
        logger.warning(f"Error delivering commands via heartbeat: {e}")

    await db.commit()

    response = {"status": "ok"}
    if pending_commands:
        response["pending_commands"] = pending_commands
    return response


# ---- OTA Update Status ----

@router.post("/{device_id}/update-status")
async def report_update_status(
    device_id: str,
    payload: UpdateStatusIn,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Receive OTA update progress from edge device."""
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


# ---- Support Request from Edge ----

@router.post("/{device_id}/support-request")
async def create_support_request(
    device_id: str,
    request: Request,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Receive a support request from an edge device."""
    body = await request.json()

    support_req = SupportRequest(
        device_id=device_id,
        alarm_id=body.get("alarm_id", ""),
        error_code=body.get("error_code", "UNKNOWN"),
        error_title=body.get("error_title", ""),
        severity=body.get("severity", "warning"),
        details=body.get("details", ""),
        user_name=body.get("user_name", ""),
        status="open",
    )
    db.add(support_req)
    await db.commit()
    await db.refresh(support_req)

    logger.info(f"Support request from {device_id}: {support_req.error_code} - {support_req.error_title}")

    return {"status": "ok", "request_id": support_req.id}


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
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Receive a batch of sensor health logs from an edge device.
    Called when edge device comes back online after offline period.
    """
    try:
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
            except Exception as e:
                logger.error(f"Health log insert error: {e}")
                continue  # Skip individual failures

        await db.commit()
        return {"received": received}
    except Exception as e:
        logger.error(f"HEALTH-LOGS ERROR for {device_id}: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ---- Health Summary (smart aggregation) ----

@router.get("/{device_id}/health-summary")
async def get_health_summary(
    device_id: str,
    hours: int = 48,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Get smart-aggregated health summary for a device.

    Instead of returning raw logs, this groups consecutive errors into
    periods and returns a concise summary per sensor:
      - If error: "FAILING for 2 days (576 errors since Mar 7)"
      - If ok: "Operating normally (since Mar 8 14:30)"
    """
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
    # Accept both cloud names and edge names (edge sends current_tag_id, current_product_id, etc.)
    tag_uid: Optional[str] = None
    current_tag_id: Optional[str] = None
    product_id: Optional[str] = None
    current_product_id: Optional[str] = None
    weight_g: Optional[float] = None
    weight_current_g: Optional[float] = None
    weight_when_placed_g: Optional[float] = None
    status: str = "empty"  # empty, occupied, in_use
    product_name: Optional[str] = None
    product_type: Optional[str] = None
    batch_number: Optional[str] = None
    can_size_ml: Optional[float] = None
    last_change_at: Optional[str] = None

    @property
    def resolved_tag_uid(self) -> Optional[str]:
        """Return tag UID from either field name."""
        return self.tag_uid or self.current_tag_id

    @property
    def resolved_product_id(self) -> Optional[str]:
        """Return product ID from either field name."""
        return self.product_id or self.current_product_id

    @property
    def resolved_weight_g(self) -> Optional[float]:
        """Return weight from either field name."""
        return self.weight_g or self.weight_current_g


class InventorySnapshotIn(BaseModel):
    slots: List[SlotState]


@router.post("/{device_id}/inventory-snapshot")
async def receive_inventory_snapshot(
    device_id: str,
    payload: InventorySnapshotIn,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Receive current slot state from edge device.

    This is a full snapshot of the device's current inventory,
    used for reconciliation and initial sync.
    """
    from app.models.can_tracking import CanTracking

    updated = 0
    for slot in payload.slots:
        tag = slot.resolved_tag_uid
        if not tag:
            continue

        prod_id = slot.resolved_product_id
        weight = slot.resolved_weight_g

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
            can.slot_id = slot.slot_id
            can.weight_current_g = weight
            can.last_seen_at = datetime.utcnow()
            if slot.status == "occupied":
                can.status = "in_stock"
            elif slot.status == "in_use":
                can.status = "in_use"
            if prod_id and not can.product_id:
                can.product_id = prod_id
        else:
            can = CanTracking(
                tag_uid=tag,
                device_id=str(device.id),
                product_id=prod_id,
                slot_id=slot.slot_id,
                weight_current_g=weight,
                weight_full_g=weight,
                status="in_stock" if slot.status == "occupied" else slot.status,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                placed_at=datetime.utcnow(),
            )
            db.add(can)

        updated += 1

    device.last_heartbeat = datetime.utcnow()
    device.status = "online"

    await db.commit()
    return {"status": "ok", "slots_processed": updated}


# ---- Mixing Sessions Sync ----

class MixingSessionIn(BaseModel):
    session_id: str
    recipe_id: str = ""
    job_id: str = ""
    user_name: str = ""
    started_at: float = 0
    completed_at: float = 0
    base_product_id: str = ""
    base_tag_id: str = ""
    base_weight_target_g: float = 0
    base_weight_actual_g: float = 0
    hardener_product_id: str = ""
    hardener_tag_id: str = ""
    hardener_weight_target_g: float = 0
    hardener_weight_actual_g: float = 0
    thinner_product_id: str = ""
    thinner_weight_g: float = 0
    ratio_achieved: float = 0
    ratio_in_spec: bool = False
    override_reason: str = ""
    application_method: str = "brush"
    pot_life_started_at: float = 0
    pot_life_expires_at: float = 0
    status: str = "completed"
    confirmation: str = "confirmed"

class MixingSessionBatch(BaseModel):
    sessions: List[MixingSessionIn]

@router.post("/{device_id}/mixing-sessions")
async def ingest_mixing_sessions(
    device_id: str,
    batch: MixingSessionBatch,
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Receive mixing sessions from edge device."""
    from app.models.mixing import MixingSessionCloud

    received = 0
    acked_ids = []

    for s in batch.sessions:
        # Deduplicate by session_uuid
        existing = await db.execute(
            select(MixingSessionCloud).where(
                MixingSessionCloud.session_uuid == s.session_id
            )
        )
        if existing.scalar_one_or_none():
            acked_ids.append(s.session_id)  # Already exists, still ack
            continue

        # Find recipe by recipe_id string (could be name or UUID)
        recipe_id_fk = None
        if s.recipe_id:
            from app.models.product import MixingRecipe
            recipe_result = await db.execute(
                select(MixingRecipe).where(
                    (MixingRecipe.id == s.recipe_id) | (MixingRecipe.name == s.recipe_id)
                )
            )
            recipe = recipe_result.scalar_one_or_none()
            if recipe:
                recipe_id_fk = recipe.id

        session_record = MixingSessionCloud(
            device_id=device.id,
            session_uuid=s.session_id,
            recipe_id=recipe_id_fk,
            job_id=s.job_id,
            user_name=s.user_name,
            started_at=datetime.utcfromtimestamp(s.started_at) if s.started_at else None,
            completed_at=datetime.utcfromtimestamp(s.completed_at) if s.completed_at else None,
            base_weight_target_g=s.base_weight_target_g,
            base_weight_actual_g=s.base_weight_actual_g,
            hardener_weight_target_g=s.hardener_weight_target_g,
            hardener_weight_actual_g=s.hardener_weight_actual_g,
            thinner_weight_g=s.thinner_weight_g,
            ratio_achieved=s.ratio_achieved,
            ratio_in_spec=s.ratio_in_spec,
            application_method=s.application_method,
            status=s.status,
        )
        db.add(session_record)
        await db.flush()  # Get session_record.id

        # --- Create InventoryAdjustments for mixing consumption ---
        if s.status == "completed" and recipe_id_fk:
            try:
                from app.models.product import MixingRecipe, Product
                from app.models.inventory import InventoryAdjustment

                recipe_obj = await db.get(MixingRecipe, recipe_id_fk)
                if recipe_obj:
                    # Base paint consumption (grams → liters using density)
                    if s.base_weight_actual_g and s.base_weight_actual_g > 0:
                        base_product = await db.get(Product, recipe_obj.base_product_id)
                        base_density = (base_product.density_g_per_ml if base_product else 1.0) or 1.0
                        base_liters = (s.base_weight_actual_g / base_density) / 1000  # g → ml → L
                        db.add(InventoryAdjustment(
                            device_id=device.id,
                            product_id=recipe_obj.base_product_id,
                            adjustment_type="mixing_consumption",
                            quantity_liters=round(base_liters, 3),
                            weight_g=s.base_weight_actual_g,
                            notes=f"Mixing session {s.session_id[:8]}",
                            created_by="auto_mix",
                        ))

                    # Hardener consumption
                    if s.hardener_weight_actual_g and s.hardener_weight_actual_g > 0:
                        hardener_product = await db.get(Product, recipe_obj.hardener_product_id)
                        hard_density = (hardener_product.density_g_per_ml if hardener_product else 1.0) or 1.0
                        hard_liters = (s.hardener_weight_actual_g / hard_density) / 1000
                        db.add(InventoryAdjustment(
                            device_id=device.id,
                            product_id=recipe_obj.hardener_product_id,
                            adjustment_type="mixing_consumption",
                            quantity_liters=round(hard_liters, 3),
                            weight_g=s.hardener_weight_actual_g,
                            notes=f"Mixing session {s.session_id[:8]}",
                            created_by="auto_mix",
                        ))

                    # Thinner consumption (if used and recipe has recommended thinner)
                    if s.thinner_weight_g and s.thinner_weight_g > 0 and recipe_obj.recommended_thinner_id:
                        thinner_product = await db.get(Product, recipe_obj.recommended_thinner_id)
                        thin_density = (thinner_product.density_g_per_ml if thinner_product else 1.0) or 1.0
                        thin_liters = (s.thinner_weight_g / thin_density) / 1000
                        db.add(InventoryAdjustment(
                            device_id=device.id,
                            product_id=recipe_obj.recommended_thinner_id,
                            adjustment_type="mixing_consumption",
                            quantity_liters=round(thin_liters, 3),
                            weight_g=s.thinner_weight_g,
                            notes=f"Mixing session {s.session_id[:8]}",
                            created_by="auto_mix",
                        ))

                    logger.info(f"Created consumption adjustments for mixing session {s.session_id[:8]}")
            except Exception as e:
                logger.warning(f"Failed to create consumption adjustments for session {s.session_id}: {e}")

        received += 1
        acked_ids.append(s.session_id)

    await db.commit()

    return {"received": received, "session_ids": acked_ids}
