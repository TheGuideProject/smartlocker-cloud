"""DeviceCommand model — stores pending commands for edge devices."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, JSON, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.database import Base


class DeviceCommand(Base):
    __tablename__ = "device_commands"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("locker_devices.id"), nullable=False)
    command_type = Column(String, nullable=False)  # product_sync, recipe_sync, config_update, ota_update, force_sync, admin_password, custom
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    delivered_at = Column(DateTime, nullable=True)
    acked_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")  # pending, delivered, acked, expired

    # Relationship
    device = relationship("LockerDevice", backref="commands")

    # Index for efficient pending-command queries
    __table_args__ = (
        Index("ix_device_commands_device_status", "device_id", "status"),
    )
