"""Product Barcode model - Links generated barcodes to products for inventory tracking."""

import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ProductBarcode(Base):
    """A barcode label generated and linked to a specific product.

    When a barcode is scanned, the system looks up barcode_data to find
    the associated product, batch, and color for inventory tracking.
    """
    __tablename__ = "product_barcodes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # The encoded barcode string (e.g., "00001/808080/SIGMAPRIME-200/RED")
    barcode_data: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)

    # Link to product catalog
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=False, index=True
    )

    # Barcode fields (denormalized for fast lookup)
    ppg_code: Mapped[str] = mapped_column(String(50), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    color: Mapped[str | None] = mapped_column(String(100), nullable=True)
    barcode_type: Mapped[str] = mapped_column(String(20), default="code128")  # code128, qr

    # Usage tracking
    times_scanned: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Metadata
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    product = relationship("Product")
