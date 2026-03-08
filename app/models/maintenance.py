"""Maintenance Chart models - Coating specifications from PPG PDFs."""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class VesselAreaType(Base):
    """Standard vessel area categories (Ballast Tank, Hull, Deck, etc.)."""
    __tablename__ = "vessel_area_types"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    iso_category: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # ISO 12944: C1-CX, Im1-Im4


class MaintenanceChart(Base):
    """A coating specification document from PPG."""
    __tablename__ = "maintenance_charts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vessel_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pdf_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    created_by = relationship("User")
    cycles = relationship(
        "CoatingCycle", back_populates="chart", cascade="all, delete-orphan"
    )


class CoatingCycle(Base):
    """A coating cycle: links a chart to a vessel area with specific layers."""
    __tablename__ = "coating_cycles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    chart_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("maintenance_charts.id"), nullable=False
    )
    area_type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vessel_area_types.id"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    chart = relationship("MaintenanceChart", back_populates="cycles")
    area_type = relationship("VesselAreaType")
    layers = relationship(
        "CoatingLayer", back_populates="cycle", cascade="all, delete-orphan",
        order_by="CoatingLayer.layer_number"
    )


class CoatingLayer(Base):
    """A single paint layer within a coating cycle (primer, intermediate, topcoat)."""
    __tablename__ = "coating_layers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coating_cycles.id"), nullable=False
    )
    layer_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3...
    layer_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # primer, intermediate, topcoat
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=False
    )
    dft_target_microns: Mapped[float] = mapped_column(Float, nullable=False)
    dft_min_microns: Mapped[float | None] = mapped_column(Float, nullable=True)
    dft_max_microns: Mapped[float | None] = mapped_column(Float, nullable=True)
    overcoat_min_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    overcoat_max_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    cycle = relationship("CoatingCycle", back_populates="layers")
    product = relationship("Product")
