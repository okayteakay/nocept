"""Tests for state.machine — ExceptionStateMachine transition enforcement."""
from __future__ import annotations

import pytest

from models.exception import ExceptionState
from state.machine import ExceptionStateMachine, InvalidTransitionError, VALID_TRANSITIONS


class TestValidTransitions:
    def test_received_to_triaged(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        result = sm.transition(ExceptionState.TRIAGED)
        assert result == ExceptionState.TRIAGED

    def test_triaged_to_researching(self):
        sm = ExceptionStateMachine(ExceptionState.TRIAGED)
        result = sm.transition(ExceptionState.RESEARCHING)
        assert result == ExceptionState.RESEARCHING

    def test_triaged_to_pending_approval(self):
        sm = ExceptionStateMachine(ExceptionState.TRIAGED)
        result = sm.transition(ExceptionState.PENDING_APPROVAL)
        assert result == ExceptionState.PENDING_APPROVAL

    def test_researching_to_pending_approval(self):
        sm = ExceptionStateMachine(ExceptionState.RESEARCHING)
        result = sm.transition(ExceptionState.PENDING_APPROVAL)
        assert result == ExceptionState.PENDING_APPROVAL

    def test_pending_approval_to_resolved(self):
        sm = ExceptionStateMachine(ExceptionState.PENDING_APPROVAL)
        result = sm.transition(ExceptionState.RESOLVED)
        assert result == ExceptionState.RESOLVED

    def test_escalated_to_resolved(self):
        sm = ExceptionStateMachine(ExceptionState.ESCALATED)
        result = sm.transition(ExceptionState.RESOLVED)
        assert result == ExceptionState.RESOLVED

    def test_current_state_updated_after_transition(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        sm.transition(ExceptionState.TRIAGED)
        assert sm.current == ExceptionState.TRIAGED


class TestInvalidTransitions:
    def test_received_to_resolved_raises(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(ExceptionState.RESOLVED)

    def test_resolved_to_any_raises(self):
        sm = ExceptionStateMachine(ExceptionState.RESOLVED)
        for state in ExceptionState:
            if state != ExceptionState.RESOLVED:
                with pytest.raises(InvalidTransitionError):
                    sm.transition(state)

    def test_can_transition_returns_false_for_invalid(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        assert sm.can_transition(ExceptionState.RESOLVED) is False

    def test_can_transition_returns_true_for_valid(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        assert sm.can_transition(ExceptionState.TRIAGED) is True

    def test_invalid_transition_error_contains_states(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(ExceptionState.RESOLVED)
        assert "received" in str(exc_info.value).lower()
        assert "resolved" in str(exc_info.value).lower()


class TestTerminalStates:
    def test_resolved_is_terminal(self):
        sm = ExceptionStateMachine(ExceptionState.RESOLVED)
        assert sm.is_terminal() is True

    def test_received_is_not_terminal(self):
        sm = ExceptionStateMachine(ExceptionState.RECEIVED)
        assert sm.is_terminal() is False

    def test_escalated_is_not_terminal(self):
        sm = ExceptionStateMachine(ExceptionState.ESCALATED)
        assert sm.is_terminal() is False
