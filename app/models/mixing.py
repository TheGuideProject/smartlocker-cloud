"""Mixing Session model - Cloud copy of mixing sessions from edge devices."""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class MixingSessionCloud(Base):
    """Mixing sessions aggregated from edge device events."""
    __tablename__ = "mixing_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=False, index=True
    )
    session_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("mixing_recipes.id"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Base component
    base_weight_target_g: Mapped[float] = mapped_column(Float, default=0.0)
    base_weight_actual_g: Mapped[float] = mapped_column(Float, default=0.0)

    # Hardener component
    hardener_weight_target_g: Mapped[float] = mapped_column(Float, default=0.0)
    hardener_weight_actual_g: Mapped[float] = mapped_column(Float, default=0.0)

    # Thinner
    thinner_weight_g: Mapped[float] = mapped_column(Float, default=0.0)

    # Results
    ratio_achieved: Mapped[float] = mapped_column(Float, default=0.0)
    ratio_in_spec: Mapped[bool] = mapped_column(Boolean, default=False)
    application_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    device = relationship("LockerDevice")
    recipe = relationship("MixingRecipe")
