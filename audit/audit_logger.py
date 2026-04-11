from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from clients.redis_client import RedisStreamsClient
from models.exception import ExceptionState
from models.resolution import Resolution

logger = logging.getLogger(__name__)

AUDIT_STREAM = "ap:audit:events"
"""Redis stream key for the append-only audit log."""


class AuditEvent(BaseModel):
    """A single event in the exception lifecycle audit trail."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exception_id: str
    event_type: str
    """
    One of:
    - "state_transition"   — exception moved between states
    - "classification"     — exception types and variances computed
    - "context_retrieved"  — supplier history pulled from Redis
    - "research_complete"  — Tavily research finished
    - "rules_applied"      — rules engine produced a decision
    - "memo_generated"     — resolution memo created
    - "resolved"           — exception auto-resolved
    - "escalated"          — exception escalated to human
    """
    previous_state: str | None = None
    new_state: str | None = None
    actor: str = "agent"
    """Either "agent" or a human analyst user ID."""
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AuditLogger:
    """Writes structured audit events to a Redis Stream.

    All entries are append-only. The stream provides an immutable, ordered
    log of every action taken on every exception, satisfying SOX audit
    trail requirements.
    """

    def __init__(self, streams: RedisStreamsClient) -> None:
        """
        Args:
            streams: A RedisStreamsClient pointed at AUDIT_STREAM.
        """
        self._streams = streams

    def log(self, event: AuditEvent) -> str:
        """Append an AuditEvent to the stream.

        Args:
            event: The event to persist.

        Returns:
            The Redis stream entry ID (e.g. "1712345678901-0").
        """
        raise NotImplementedError

    def log_transition(
        self,
        exception_id: str,
        from_state: ExceptionState,
        to_state: ExceptionState,
        details: dict | None = None,
    ) -> str:
        """Convenience method to log a state transition event.

        Args:
            exception_id: UUID of the affected exception.
            from_state: The state being left.
            to_state: The state being entered.
            details: Optional additional context.

        Returns:
            The Redis stream entry ID.
        """
        raise NotImplementedError

    def log_resolution(self, resolution: Resolution) -> str:
        """Log the final resolution of an exception.

        Args:
            resolution: The completed Resolution record.

        Returns:
            The Redis stream entry ID.
        """
        raise NotImplementedError

    def get_exception_trail(self, exception_id: str) -> list[AuditEvent]:
        """Return all audit events for a specific exception, oldest first.

        Args:
            exception_id: UUID of the exception.

        Returns:
            Ordered list of AuditEvent objects.
        """
        raise NotImplementedError

    def get_recent_events(self, count: int = 100) -> list[AuditEvent]:
        """Return the most recent audit events across all exceptions.

        Args:
            count: Maximum number of events to return.

        Returns:
            List of AuditEvent objects, most recent last (stream order).
        """
        raise NotImplementedError
