"""Tests for Phase 2 Step 7: Redis exception queue + first audit event."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent.pipeline import detect_and_enqueue_exception
from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient
from models.exception import ExceptionState


@pytest.fixture
def audit(fake_redis) -> AuditLogger:
    streams = RedisStreamsClient(fake_redis, "ap:audit:events")
    return AuditLogger(streams)


def test_detected_exception_written_to_queue_with_required_fields(
    informal_mod_triple, store, audit, app_config
):
    invoice, po, grn = informal_mod_triple
    exc = detect_and_enqueue_exception(invoice, po, grn, store, audit, app_config)

    assert exc is not None
    assert exc.state == ExceptionState.RECEIVED

    queue_record = store.get_queue_record(exc.exception_id)
    assert queue_record is not None
    assert queue_record["exception_id"] == exc.exception_id
    assert queue_record["po_number"] == po.po_number
    assert queue_record["invoice_number"] == invoice.invoice_number
    assert queue_record["supplier_id"] == invoice.supplier_id
    assert queue_record["exception_type"] == exc.exception_types[0].value
    assert queue_record["status"] == "received"

    # Required numeric/time fields are persisted as strings in Redis hashes.
    assert float(queue_record["variance_amount"]) >= 0
    assert float(queue_record["variance_percentage"]) >= 0
    assert datetime.fromisoformat(queue_record["timestamp"])


def test_detection_event_is_first_audit_entry_for_exception(
    informal_mod_triple, store, audit, app_config
):
    invoice, po, grn = informal_mod_triple
    exc = detect_and_enqueue_exception(invoice, po, grn, store, audit, app_config)

    assert exc is not None
    trail = audit.get_exception_trail(exc.exception_id)
    assert len(trail) == 1

    first_event = trail[0]
    assert first_event.event_type == "classification"
    assert first_event.new_state == "received"
    assert first_event.details["po_number"] == po.po_number
    assert first_event.details["invoice_number"] == invoice.invoice_number
    assert first_event.details["supplier_id"] == invoice.supplier_id
    assert first_event.details["status"] == "received"


def test_straight_through_invoice_not_enqueued(sample_invoice, sample_po, sample_grn, store, audit, app_config):
    exc = detect_and_enqueue_exception(
        sample_invoice, sample_po, sample_grn, store, audit, app_config
    )
    assert exc is None
    assert store.list_queue_ids() == []
    assert audit.get_recent_events() == []

