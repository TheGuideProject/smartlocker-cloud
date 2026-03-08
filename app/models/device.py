"""Locker Device model - Edge devices (Raspberry Pi) on vessels."""

import uuid
import secrets
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey
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

    # Relationships
    vessel = relationship("Vessel", back_populates="devices")
    events = relationship("DeviceEvent", back_populates="device")

    @staticmethod
    def generate_api_key() -> str:
        """Generate a new API key for device authentication."""
        return f"slk_{secrets.token_urlsafe(32)}"
