"""
WebSocket endpoint for real-time bidirectional sync between edge devices and cloud.

Handles:
- Edge -> Cloud: events, heartbeat, mixing sessions, inventory snapshots, health logs
- Cloud -> Edge: commands (product sync, recipe sync, config update, OTA)
- Both: ack messages for delivery confirmation
"""

import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.device import LockerDevice
from app.models.command import DeviceCommand

logger = logging.getLogger("smartlocker.websocket")

router = APIRouter()


class ConnectionManager:
    """Tracks active WebSocket connections per device."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, device_id: str, ws: WebSocket):
        """Accept and register a new WebSocket connection."""
        await ws.accept()
        # Close any existing stale connection for this device
        old = self._connections.get(device_id)
        if old:
            try:
                await old.close(code=1000, reason="Replaced by new connection")
            except Exception:
                pass
        self._connections[device_id] = ws
        logger.info(f"[WS] Device {device_id} connected ({len(self._connections)} total)")

    def disconnect(self, device_id: str):
        """Remove a device connection."""
        self._connections.pop(device_id, None)
        logger.info(f"[WS] Device {device_id} disconnected ({len(self._connections)} total)")

    async def send_to_device(self, device_id: str, message: dict) -> bool:
        """Send a JSON message to a specific device. Returns True if sent."""
        ws = self._connections.get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            self.disconnect(device_id)
            return False

    def is_connected(self, device_id: str) -> bool:
        """Check if a device is currently connected."""
        return device_id in self._connections

    def get_connected_devices(self) -> list:
        """Return list of connected device IDs."""
        return list(self._connections.keys())


# Module-level singleton
manager = ConnectionManager()


async def _verify_ws_auth(device_id: str, api_key: str) -> LockerDevice | None:
    """Verify device API key for WebSocket auth."""
    async with async_session() as db:
        result = await db.execute(
            select(LockerDevice).where(LockerDevice.api_key_hash == api_key)
        )
        device = result.scalar_one_or_none()
        if device and device.device_id == device_id:
            return device
        # Also check by device_id match (api_key should still match)
        if device:
            return device
    return None


async def _send_pending_commands(ws: WebSocket, device_db_id: str):
    """Send all pending DeviceCommands to the device."""
    async with async_session() as db:
        result = await db.execute(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device_db_id,
                DeviceCommand.status == "pending",
            ).order_by(DeviceCommand.created_at)
        )
        commands = result.scalars().all()

        for cmd in commands:
            try:
                await ws.send_json({
                    "type": "command",
                    "command_id": cmd.id,
                    "command_type": cmd.command_type,
                    "payload": cmd.payload or {},
                })
                cmd.status = "delivered"
                cmd.delivered_at = datetime.utcnow()
            except Exception as e:
                logger.error(f"[WS] Error sending command {cmd.id}: {e}")
                break

        await db.commit()
        if commands:
            logger.info(f"[WS] Sent {len(commands)} pending commands to device {device_db_id}")


async def _handle_message(device_db_id: str, device_id: str, data: dict):
    """Process a message received from an edge device via WebSocket."""
    from app.services.sync_service import (
        process_event_batch, process_heartbeat, process_mixing_sessions,
        process_health_logs, process_inventory_snapshot,
    )

    msg_type = data.get("type", "")

    async with async_session() as db:
        # Fetch fresh device reference
        result = await db.execute(
            select(LockerDevice).where(LockerDevice.id == device_db_id)
        )
        device = result.scalar_one_or_none()
        if not device:
            logger.error(f"[WS] Device {device_db_id} not found in DB")
            return

        if msg_type == "event_batch":
            events = data.get("events", [])
            received, duplicates, acked_ids = await process_event_batch(db, device, events)
            await db.commit()
            # Send ack
            await manager.send_to_device(device_id, {
                "type": "ack",
                "event_ids": acked_ids,
            })
            logger.info(f"[WS] Events: {received} new, {duplicates} dupes from {device_id}")

        elif msg_type == "heartbeat":
            hb_data = data.get("data", {})
            await process_heartbeat(db, device, hb_data)
            await db.commit()
            logger.debug(f"[WS] Heartbeat from {device_id}")

        elif msg_type == "mixing_sessions":
            sessions = data.get("sessions", [])
            received, acked_ids = await process_mixing_sessions(db, device, sessions)
            await db.commit()
            await manager.send_to_device(device_id, {
                "type": "ack_mixing",
                "session_ids": acked_ids,
            })
            logger.info(f"[WS] Mixing sessions: {received} new from {device_id}")

        elif msg_type == "inventory_snapshot":
            slots = data.get("slots", [])
            processed = await process_inventory_snapshot(db, device, slots)
            device.last_heartbeat = datetime.utcnow()
            device.status = "online"
            await db.commit()
            logger.info(f"[WS] Inventory snapshot: {processed} slots from {device_id}")

        elif msg_type == "health_logs":
            logs = data.get("logs", [])
            received = await process_health_logs(db, device, logs)
            await db.commit()
            logger.info(f"[WS] Health logs: {received} from {device_id}")

        elif msg_type == "ack":
            # Device acked a command we sent
            cmd_id = data.get("command_id", "")
            if cmd_id:
                cmd_result = await db.execute(
                    select(DeviceCommand).where(DeviceCommand.id == cmd_id)
                )
                cmd = cmd_result.scalar_one_or_none()
                if cmd:
                    cmd.status = "acked"
                    cmd.acked_at = datetime.utcnow()
                    await db.commit()
                    logger.debug(f"[WS] Command {cmd_id} acked by {device_id}")

        else:
            logger.warning(f"[WS] Unknown message type '{msg_type}' from {device_id}")


@router.websocket("/api/devices/{device_id}/ws")
async def device_websocket(websocket: WebSocket, device_id: str):
    """
    WebSocket endpoint for real-time bidirectional sync with an edge device.

    Auth: Pass api_key as query parameter: ws://host/api/devices/DEV-001/ws?api_key=slk_xxx
    """
    # 1. Get API key from query params
    api_key = websocket.query_params.get("api_key", "")
    if not api_key:
        await websocket.close(code=4001, reason="Missing api_key parameter")
        return

    # 2. Verify credentials
    device = await _verify_ws_auth(device_id, api_key)
    if not device:
        await websocket.close(code=4003, reason="Invalid device or API key")
        return

    device_db_id = device.id

    # 3. Accept and register connection
    await manager.connect(device_id, websocket)

    # 4. Update device status to online
    async with async_session() as db:
        result = await db.execute(
            select(LockerDevice).where(LockerDevice.id == device_db_id)
        )
        dev = result.scalar_one_or_none()
        if dev:
            dev.status = "online"
            dev.last_heartbeat = datetime.utcnow()
            await db.commit()

    # 5. Send pending commands
    try:
        await _send_pending_commands(websocket, device_db_id)
    except Exception as e:
        logger.error(f"[WS] Error sending pending commands: {e}")

    # 6. Message loop
    try:
        while True:
            data = await websocket.receive_json()
            try:
                await _handle_message(device_db_id, device_id, data)
            except Exception as e:
                logger.error(f"[WS] Error handling message from {device_id}: {e}")
    except WebSocketDisconnect:
        logger.info(f"[WS] Device {device_id} disconnected normally")
    except Exception as e:
        logger.error(f"[WS] Unexpected error for {device_id}: {e}")
    finally:
        manager.disconnect(device_id)
        # Update device status
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(LockerDevice).where(LockerDevice.id == device_db_id)
                )
                dev = result.scalar_one_or_none()
                if dev:
                    dev.status = "offline"
                    await db.commit()
        except Exception:
            pass
