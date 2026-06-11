"""Cache of technical product specs fetched from Product Equivalence.

The cloud calls Product Equivalence by product NAME to get coverage (m²/L),
volume solids, density, etc. Results are cached here so devices keep working
when Product Equivalence is slow or down, and so we don't re-query for every
m²-to-litres calculation.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Float, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProductSpecCache(Base):
    __tablename__ = "product_spec_cache"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Normalized lookup key (lowercased, trimmed product name).
    query_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    query_name: Mapped[str] = mapped_column(String(255), nullable=False)

    matched_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # exact|contains|none
    coverage_m2_per_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # datasheet|computed|none
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)  # high|medium|low
    needs_validation: Mapped[bool] = mapped_column(Boolean, default=True)
    specs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidates_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
