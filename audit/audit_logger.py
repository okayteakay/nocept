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
        fields = {
            "event_id": event.event_id,
            "exception_id": event.exception_id,
            "event_type": event.event_type,
            "previous_state": event.previous_state or "",
            "new_state": event.new_state or "",
            "actor": event.actor,
            "details": json.dumps(event.details),
            "timestamp": event.timestamp.isoformat(),
        }
        entry_id = self._streams.append(fields)
        logger.debug(
            "Audit event appended (entry_id=%s, exception_id=%s, event_type=%s)",
            entry_id,
            event.exception_id,
            event.event_type,
        )
        return entry_id

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
        event_type = (
            "resolved"
            if resolution.memo.action != ResolutionAction.ESCALATE_TO_HUMAN
            else "escalated"
        )
        event = AuditEvent(
            exception_id=resolution.exception_id,
            event_type=event_type,
            details={
                "action": resolution.memo.action.value,
                "confidence": resolution.memo.confidence,
                "root_cause": resolution.memo.root_cause.value,
            },
        )
        return self.log(event)

    def log_detection(self, exc: InvoiceException) -> str:
        """Log initial exception detection as the first audit-trail event."""
        event = AuditEvent(
            exception_id=exc.exception_id,
            event_type="classification",
            previous_state=None,
            new_state=exc.state.value,
            details={
                "po_number": exc.purchase_order.po_number,
                "invoice_number": exc.invoice.invoice_number,
                "supplier_id": exc.invoice.supplier_id,
                "exception_types": [t.value for t in exc.exception_types],
                "variance_amount": round(abs(exc.total_variance_usd), 2),
                "variance_percentage": _variance_percentage(exc),
                "status": exc.state.value,
                "detected_at": exc.created_at.isoformat(),
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
        entries = self._streams.read_range()
        trail: list[AuditEvent] = []
        for entry in entries:
            fields = entry["fields"]
            if fields.get("exception_id") != exception_id:
                continue
            trail.append(_event_from_fields(fields))
        return trail

    def get_recent_events(self, count: int = 100) -> list[AuditEvent]:
        """Return the most recent audit events across all exceptions.

        Args:
            count: Maximum number of events to return.

        Returns:
            List of AuditEvent objects, most recent last (stream order).
        """
        entries = self._streams.read_range()
        recent = entries[-count:] if count > 0 else entries
        return [_event_from_fields(entry["fields"]) for entry in recent]


def _event_from_fields(fields: dict) -> AuditEvent:
    details_raw = fields.get("details", "{}")
    if isinstance(details_raw, str):
        try:
            details = json.loads(details_raw)
        except json.JSONDecodeError:
            details = {}
    else:
        details = details_raw
    ts_raw = fields.get("timestamp")
    timestamp = (
        datetime.fromisoformat(ts_raw)
        if isinstance(ts_raw, str) and ts_raw
        else datetime.now(timezone.utc)
    )
    return AuditEvent(
        event_id=fields.get("event_id", str(uuid.uuid4())),
        exception_id=fields.get("exception_id", ""),
        event_type=fields.get("event_type", ""),
        previous_state=fields.get("previous_state") or None,
        new_state=fields.get("new_state") or None,
        actor=fields.get("actor", "agent"),
        details=details if isinstance(details, dict) else {},
        timestamp=timestamp,
    )


def _variance_percentage(exc: InvoiceException) -> float:
    po_total = exc.purchase_order.total_amount
    if po_total <= 0:
        return 0.0
    return round((abs(exc.total_variance_usd) / po_total) * 100, 2)
