"""Exception state machine — transition graph and validation.

The graph is the single source of truth for valid lifecycle transitions.
The actual transition enforcement lives in ``state.redis_backend.RedisStateStore.transition``,
which validates moves against ``VALID_TRANSITIONS`` before persisting.
"""
from __future__ import annotations

from models.exception import ExceptionState

# All legal state transitions for the invoice exception lifecycle.
# Terminal states map to an empty set.
VALID_TRANSITIONS: dict[ExceptionState, set[ExceptionState]] = {
    ExceptionState.RECEIVED: {ExceptionState.TRIAGED},
    ExceptionState.TRIAGED: {
        ExceptionState.RESEARCHING,
        ExceptionState.PENDING_APPROVAL,
        ExceptionState.ESCALATED,
        ExceptionState.RESOLVED,
    },
    ExceptionState.RESEARCHING: {
        ExceptionState.PENDING_APPROVAL,
        ExceptionState.ESCALATED,
    },
    ExceptionState.PENDING_APPROVAL: {
        ExceptionState.RESOLVED,
        ExceptionState.ESCALATED,
        ExceptionState.APPROVED,
        ExceptionState.REJECTED,
    },
    ExceptionState.ESCALATED: {
        ExceptionState.APPROVED,
        ExceptionState.REJECTED,
        ExceptionState.RESOLVED,
    },
    ExceptionState.APPROVED: set(),
    ExceptionState.REJECTED: set(),
    ExceptionState.RESOLVED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not permitted."""

    def __init__(self, from_state: ExceptionState, to_state: ExceptionState) -> None:
        super().__init__(
            f"Invalid transition: {from_state.value!r} → {to_state.value!r}. "
            f"Allowed targets: {[s.value for s in VALID_TRANSITIONS.get(from_state, set())]}"
        )
        self.from_state = from_state
        self.to_state = to_state
