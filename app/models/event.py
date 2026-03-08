"""Device Event model - Events received from edge devices."""

import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class DeviceEvent(Base):
    """Immutable event log from edge devices. UUID-deduplicated."""
    __tablename__ = "device_events"
    __table_args__ = (
        UniqueConstraint("device_id", "event_uuid", name="uq_device_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=False, index=True
    )
    event_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    shelf_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slot_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tag_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    device = relationship("LockerDevice", back_populates="events")
