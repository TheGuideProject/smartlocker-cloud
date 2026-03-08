"""Pairing Code model - 6-digit codes for device registration."""

import uuid
import random
import string
from datetime import datetime, timedelta
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    code: Mapped[str] = mapped_column(
        String(6), unique=True, nullable=False
    )  # 6-digit alphanumeric code (e.g., "A3K7M2")
    vessel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vessels.id"), nullable=False
    )
    device_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # Pre-assigned name for the device

    # Status
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by_device_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Validity
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    vessel = relationship("Vessel")
    device = relationship("LockerDevice", foreign_keys=[used_by_device_id])

    @staticmethod
    def generate_code() -> str:
        """Generate a 6-character alphanumeric code (uppercase, no ambiguous chars)."""
        # Remove ambiguous characters: 0/O, 1/I/L
        safe_chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return ''.join(random.choices(safe_chars, k=6))

    @staticmethod
    def default_expiry() -> datetime:
        """Default expiry: 48 hours from now."""
        return datetime.utcnow() + timedelta(hours=48)

    @property
    def is_valid(self) -> bool:
        """Check if code is still usable."""
        return (
            not self.is_used
            and datetime.utcnow() < self.expires_at
        )

    @property
    def status_label(self) -> str:
        if self.is_used:
            return "used"
        if datetime.utcnow() >= self.expires_at:
            return "expired"
        return "active"
