"""Product and Mixing Recipe models - PPG paint catalog."""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ppg_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # base_paint, hardener, thinner, primer
    density_g_per_ml: Mapped[float] = mapped_column(Float, default=1.0)
    pot_life_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hazard_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    can_sizes_ml: Mapped[dict | None] = mapped_column(JSON, default=list)
    can_tare_weight_g: Mapped[dict | None] = mapped_column(JSON, default=dict)
    colors_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=list
    )  # [{"name": "Redbrown 6179", "hex": "#B5462A"}, ...]
    sds_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class MixingRecipe(Base):
    __tablename__ = "mixing_recipes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=False
    )
    hardener_product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=False
    )
    ratio_base: Mapped[float] = mapped_column(Float, nullable=False)  # e.g., 4.0
    ratio_hardener: Mapped[float] = mapped_column(Float, nullable=False)  # e.g., 1.0
    tolerance_pct: Mapped[float] = mapped_column(Float, default=5.0)
    thinner_pct_brush: Mapped[float] = mapped_column(Float, default=5.0)
    thinner_pct_roller: Mapped[float] = mapped_column(Float, default=5.0)
    thinner_pct_spray: Mapped[float] = mapped_column(Float, default=10.0)
    recommended_thinner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=True
    )
    pot_life_minutes: Mapped[int] = mapped_column(Integer, default=480)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    base_product = relationship("Product", foreign_keys=[base_product_id])
    hardener_product = relationship("Product", foreign_keys=[hardener_product_id])
    thinner_product = relationship("Product", foreign_keys=[recommended_thinner_id])
