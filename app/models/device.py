"""Locker Device model - Edge devices (Raspberry Pi) on vessels."""

import uuid
import secrets
from datetime import datetime, timedelta
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class LockerDevice(Base):
    __tablename__ = "locker_devices"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    vessel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vessels.id"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )  # Matches edge DEVICE_ID (e.g., "LOCKER-DEV-001")
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    software_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="offline"
    )  # online, delayed, offline, maintenance
    installed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ---- Device Monitoring Fields ----
    driver_status: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # {"rfid": "real", "weight": "fake", "led": "fake", "buzzer": "fake"}
    sensor_health: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # Per-sensor health data from edge heartbeats
    system_info: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # {"uptime_seconds": ..., "events_pending_sync": ..., "db_size_mb": ...}
    pending_admin_password: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # One-time password push to device on next config sync

    # Relationships
    vessel = relationship("Vessel", back_populates="devices")
    events = relationship("DeviceEvent", back_populates="device")

    @staticmethod
    def generate_api_key() -> str:
        """Generate a new API key for device authentication."""
        return f"slk_{secrets.token_urlsafe(32)}"

    @property
    def is_online(self) -> bool:
        """Device is online if last heartbeat was within 2 minutes."""
        if not self.last_heartbeat:
            return False
        return (datetime.utcnow() - self.last_heartbeat) < timedelta(minutes=2)

    @property
    def last_seen_ago(self) -> str:
        """Human-readable time since last heartbeat."""
        if not self.last_heartbeat:
            return "Never"
        delta = datetime.utcnow() - self.last_heartbeat
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
