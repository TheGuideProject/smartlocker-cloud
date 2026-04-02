"""Device Log model - Application logs uploaded from edge devices for remote debugging."""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class DeviceLog(Base):
    """
    Stores application log lines uploaded from edge devices.

    Edge devices buffer recent log lines and periodically upload them
    to the cloud. Admins can view these in the Device Logs page for
    remote debugging without SSH access to the device.
    """
    __tablename__ = "device_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("locker_devices.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True
    )  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    logger_name: Mapped[str] = mapped_column(
        String(100), default=""
    )  # e.g. "smartlocker.sensor", "smartlocker.sync"
    message: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    device = relationship("LockerDevice", backref="device_logs")
