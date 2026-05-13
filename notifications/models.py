"""Notification data models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class NotificationChannel(str, Enum):
    """Channel for notification delivery."""
    SLACK = "slack"
    EMAIL = "email"
    DASHBOARD = "dashboard"


class NotificationEvent(str, Enum):
    """Event types that trigger notifications."""
    ESCALATION = "escalation"
    APPROVAL = "approval"
    REJECTION = "rejection"
    SLA_BREACH = "sla_breach"
    RULE_TRIGGERED = "rule_triggered"


class Notification(BaseModel):
    """A notification record."""
    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exception_id: str | None = None
    recipient: str  # Email or Slack user ID
    channel: NotificationChannel
    event_type: NotificationEvent
    subject: str
    message: str
    action_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: datetime | None = None
    read_at: datetime | None = None
