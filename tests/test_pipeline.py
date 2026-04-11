"""Tests for agent.pipeline — end-to-end resolution workflow."""
from __future__ import annotations

import pytest

from agent.pipeline import PipelineResult, run_pipeline
from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient
from models.exception import ExceptionState
from models.resolution import ResolutionAction, RootCause


@pytest.fixture
def audit(fake_redis) -> AuditLogger:
    streams = RedisStreamsClient(fake_redis, "ap:audit:events")
    return AuditLogger(streams)


class TestStraightThroughPipeline:
    def test_straight_through_resolves_auto_approve(
        self, sample_invoice, sample_po, sample_grn, store, mock_tavily, audit, app_config
    ):
        """A matching invoice should resolve with AUTO_APPROVE."""
        ...

    def test_straight_through_final_state_resolved(
        self, sample_invoice, sample_po, sample_grn, store, mock_tavily, audit, app_config
    ):
        ...

    def test_straight_through_exception_persisted_in_store(
        self, sample_invoice, sample_po, sample_grn, store, mock_tavily, audit, app_config
    ):
        ...

    def test_straight_through_elapsed_seconds_recorded(
        self, sample_invoice, sample_po, sample_grn, store, mock_tavily, audit, app_config
    ):
        ...


class TestInformalModificationPipeline:
    def test_informal_modification_resolved_with_research(
        self, informal_mod_triple, store, tavily_with_results, audit, app_config
    ):
        ...

    def test_informal_modification_root_cause_correct(
        self, informal_mod_triple, store, tavily_with_results, audit, app_config
    ):
        ...

    def test_informal_modification_memo_has_evidence(
        self, informal_mod_triple, store, tavily_with_results, audit, app_config
    ):
        ...


class TestStateTransitionsInOrder:
    def test_exception_passes_through_triaged_and_researching(
        self, informal_mod_triple, store, mock_tavily, audit, app_config
    ):
        """Audit log should contain transitions: RECEIVED → TRIAGED → RESEARCHING → RESOLVED."""
        ...

    def test_audit_events_written_per_step(
        self, informal_mod_triple, store, mock_tavily, audit, app_config
    ):
        ...

    def test_no_invalid_transitions_in_audit_trail(
        self, informal_mod_triple, store, mock_tavily, audit, app_config
    ):
        ...


class TestEscalationPath:
    def test_high_variance_no_evidence_escalated(
        self, price_variance_triple, store, mock_tavily, audit, app_config
    ):
        ...

    def test_escalated_final_state_is_escalated(
        self, price_variance_triple, store, mock_tavily, audit, app_config
    ):
        ...
