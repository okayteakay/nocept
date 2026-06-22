from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from clients.redis_client import RedisStreamsClient
from models.exception import ExceptionState, InvoiceException
from models.resolution import Resolution, ResolutionAction

logger = logging.getLogger(__name__)

AUDIT_STREAM = "ap:audit:events"
"""Redis stream key for the append-only audit log."""


class AuditEvent(BaseModel):
    """A single event in the exception lifecycle audit trail."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exception_id: str | None = None
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
        # Redis Streams require flat string key-value pairs
        fields = {
            "event_id": event.event_id,
            "exception_id": event.exception_id if event.exception_id else "",
            "event_type": event.event_type,
            "previous_state": str(event.previous_state) if event.previous_state else "",
            "new_state": str(event.new_state) if event.new_state else "",
            "actor": event.actor,
            "timestamp": event.timestamp.isoformat(),
            "details": json.dumps(event.details),
        }
        return self._streams.append(fields)

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
        event = AuditEvent(
            exception_id=exception_id,
            event_type="state_transition",
            previous_state=from_state.value,
            new_state=to_state.value,
            details=details or {},
        )
        return self.log(event)

    def log_resolution(self, resolution: Resolution) -> str:
        """Log the final resolution of an exception.

        Args:
            resolution: The completed Resolution record.

        Returns:
            The Redis stream entry ID.
        """
        event = AuditEvent(
            exception_id=resolution.exception_id,
            event_type="resolved" if resolution.final_state == ExceptionState.RESOLVED else "escalated",
            details={
                "final_state": resolution.final_state.value,
                "root_cause": resolution.memo.root_cause.value,
                "action": resolution.memo.action.value,
            },
        )
        return self.log(event)

    def get_exception_trail(self, exception_id: str) -> list[AuditEvent]:
        """Return all audit events for a specific exception, oldest first.

        Args:
            exception_id: UUID of the exception.

        Returns:
            Ordered list of AuditEvent objects.
        """
        all_events = self._streams.read_range()
        trail = []
        for entry in all_events:
            fields = entry["fields"]
            if fields.get("exception_id") == exception_id:
                # Reconstruct AuditEvent from fields
                trail.append(AuditEvent(
                    event_id=fields["event_id"],
                    exception_id=fields["exception_id"],
                    event_type=fields["event_type"],
                    previous_state=fields["previous_state"] or None,
                    new_state=fields["new_state"] or None,
                    actor=fields["actor"],
                    timestamp=datetime.fromisoformat(fields["timestamp"]),
                    details=json.loads(fields["details"]),
                ))
        return trail

    def get_recent_events(self, count: int = 100) -> list[AuditEvent]:
        """Return the most recent audit events across all exceptions.

        Args:
            count: Maximum number of events to return.

        Returns:
            List of AuditEvent objects, most recent last (stream order).
        """
        entries = self._streams.read_range()
        recent = entries[-count:]

        trail = []
        for entry in recent:
            fields = entry["fields"]
            trail.append(AuditEvent(
                event_id=fields["event_id"],
                exception_id=fields["exception_id"],
                event_type=fields["event_type"],
                previous_state=fields["previous_state"] or None,
                new_state=fields["new_state"] or None,
                actor=fields["actor"],
                timestamp=datetime.fromisoformat(fields["timestamp"]),
                details=json.loads(fields["details"]),
            ))
        return trail
