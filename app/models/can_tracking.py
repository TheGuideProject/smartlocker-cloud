"""Can-level traceability model - Tracks individual paint cans via RFID."""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CanTracking(Base):
    """Tracks individual paint cans throughout their lifecycle."""
    __tablename__ = "can_tracking"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tag_uid: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    lot_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=True
    )
    device_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=True
    )

    # Current state
    status: Mapped[str] = mapped_column(
        String(30), default="in_stock"
    )  # in_stock, in_use, consumed, removed
    slot_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Weight tracking
    weight_full_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_current_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_tare_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    can_size_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Lifecycle timestamps
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Consumption
    total_consumed_g: Mapped[float] = mapped_column(Float, default=0.0)
    times_used: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    product = relationship("Product")
    device = relationship("LockerDevice")
