"""Tests for state.machine — transition graph and validation."""
from __future__ import annotations

import pytest

from models.exception import ExceptionState
from state.machine import VALID_TRANSITIONS, InvalidTransitionError


class TestValidTransitions:
    def test_received_to_triaged(self):
        assert ExceptionState.TRIAGED in VALID_TRANSITIONS[ExceptionState.RECEIVED]

    def test_triaged_to_researching(self):
        assert ExceptionState.RESEARCHING in VALID_TRANSITIONS[ExceptionState.TRIAGED]

    def test_triaged_to_pending_approval(self):
        assert ExceptionState.PENDING_APPROVAL in VALID_TRANSITIONS[ExceptionState.TRIAGED]

    def test_researching_to_pending_approval(self):
        assert ExceptionState.PENDING_APPROVAL in VALID_TRANSITIONS[ExceptionState.RESEARCHING]

    def test_pending_approval_to_resolved(self):
        assert ExceptionState.RESOLVED in VALID_TRANSITIONS[ExceptionState.PENDING_APPROVAL]

    def test_escalated_to_resolved(self):
        assert ExceptionState.RESOLVED in VALID_TRANSITIONS[ExceptionState.ESCALATED]


class TestInvalidTransitions:
    def test_received_to_resolved_not_allowed(self):
        assert ExceptionState.RESOLVED not in VALID_TRANSITIONS[ExceptionState.RECEIVED]

    def test_resolved_is_terminal(self):
        assert VALID_TRANSITIONS[ExceptionState.RESOLVED] == set()

    def test_approved_is_terminal(self):
        assert VALID_TRANSITIONS[ExceptionState.APPROVED] == set()

    def test_rejected_is_terminal(self):
        assert VALID_TRANSITIONS[ExceptionState.REJECTED] == set()

    def test_invalid_transition_error_contains_states(self):
        err = InvalidTransitionError(ExceptionState.RECEIVED, ExceptionState.RESOLVED)
        msg = str(err).lower()
        assert "received" in msg
        assert "resolved" in msg
        assert err.from_state == ExceptionState.RECEIVED
        assert err.to_state == ExceptionState.RESOLVED
