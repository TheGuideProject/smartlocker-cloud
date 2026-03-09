"""Support Request model - tracks PPG support requests from devices."""

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class SupportRequest(Base):
    __tablename__ = "support_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(100), ForeignKey("locker_devices.device_id"), nullable=False)
    alarm_id = Column(String(100))  # From edge alarm_manager
    error_code = Column(String(10), nullable=False)  # E001, E020, etc.
    error_title = Column(String(255))
    severity = Column(String(20))  # critical, warning, info
    details = Column(Text)
    user_name = Column(String(100))  # Crew member who requested

    status = Column(String(20), default="open")  # open, in_progress, resolved, closed
    resolution_notes = Column(Text)
    resolved_by = Column(String(100))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))

    # Relationship
    device = relationship("LockerDevice", backref="support_requests")

    @property
    def status_label(self):
        labels = {"open": "Open", "in_progress": "In Progress", "resolved": "Resolved", "closed": "Closed"}
        return labels.get(self.status, self.status)

    @property
    def is_open(self):
        return self.status in ("open", "in_progress")
