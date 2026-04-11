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
    },
    ExceptionState.RESEARCHING: {
        ExceptionState.PENDING_APPROVAL,
        ExceptionState.ESCALATED,
    },
    ExceptionState.PENDING_APPROVAL: {
        ExceptionState.RESOLVED,
        ExceptionState.ESCALATED,
    },
    ExceptionState.RESOLVED: set(),
    ExceptionState.ESCALATED: {ExceptionState.RESOLVED},
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


class ExceptionStateMachine:
    """Enforces valid lifecycle transitions for an InvoiceException.

    Usage::

        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        sm.transition(ExceptionState.TRIAGED)
        assert sm.current == ExceptionState.TRIAGED
    """

    def __init__(self, current_state: ExceptionState) -> None:
        self._state = current_state

    @property
    def current(self) -> ExceptionState:
        """The current state of this machine."""
        return self._state

    def can_transition(self, to: ExceptionState) -> bool:
        """Return True if transitioning to *to* is permitted from the current state."""
        return to in VALID_TRANSITIONS.get(self._state, set())

    def transition(self, to: ExceptionState) -> ExceptionState:
        """Attempt to move to state *to*.

        Args:
            to: The desired next state.

        Returns:
            The new current state.

        Raises:
            InvalidTransitionError: If the transition is not permitted.
        """
        if not self.can_transition(to):
            raise InvalidTransitionError(self._state, to)
        self._state = to
        return self._state

    def is_terminal(self) -> bool:
        """Return True if the current state has no outgoing transitions."""
        return not VALID_TRANSITIONS.get(self._state, set())
