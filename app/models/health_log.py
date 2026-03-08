"""Sensor Health Log model - Offline health data uploaded from edge devices."""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class SensorHealthLog(Base):
    """
    Stores sensor health snapshots uploaded from edge devices.

    Edge devices log health every 5 minutes to local SQLite.
    When they reconnect, they batch-upload all buffered logs here.
    The cloud aggregates these into smart summaries for the admin dashboard.
    """
    __tablename__ = "sensor_health_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )  # When the reading was taken on the edge
    sensor: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # rfid, weight, led, buzzer, weight_shelf_1, etc.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # ok, warning, error, disconnected, out_of_range
    message: Mapped[str] = mapped_column(Text, default='')
    value: Mapped[str] = mapped_column(Text, default='')
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    # Relationships
    device = relationship("LockerDevice", backref="health_logs")
