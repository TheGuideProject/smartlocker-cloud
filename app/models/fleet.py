"""Fleet and Vessel models."""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Fleet(Base):
    __tablename__ = "fleets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    company_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("companies.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    company = relationship("Company", back_populates="fleets")
    vessels = relationship("Vessel", back_populates="fleet", cascade="all, delete-orphan")


class Vessel(Base):
    __tablename__ = "vessels"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    fleet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fleets.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    imo_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    vessel_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flag_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    class_society: Mapped[str | None] = mapped_column(String(50), nullable=True)
    built_year: Mapped[int | None] = mapped_column(nullable=True)
    dwt: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    fleet = relationship("Fleet", back_populates="vessels")
    devices = relationship("LockerDevice", back_populates="vessel")
