"""Unit tests for human approval workflow endpoints."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from audit.audit_logger import AuditLogger, AuditEvent
from models.exception import ExceptionState, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder


@pytest.fixture
def escalated_exception(sample_po, sample_invoice, sample_grn) -> InvoiceException:
    """Create an exception in ESCALATED state for testing human approval."""
    exc = InvoiceException(
        invoice=sample_invoice,
        purchase_order=sample_po,
        grn=sample_grn,
        state=ExceptionState.ESCALATED,
        total_variance_usd=150.0,
    )
    return exc


@pytest.fixture
def audit_logger(fake_redis) -> AuditLogger:
    """Create an AuditLogger for testing."""
    from clients.redis_client import RedisStreamsClient
    streams = RedisStreamsClient(fake_redis, "test:audit")
    return AuditLogger(streams)


class TestApproveEndpoint:
    """Tests for POST /tools/approve/{exception_id}"""

    @pytest.mark.asyncio
    async def test_approve_escalated_exception(self, store, fake_redis, audit_logger, escalated_exception):
        """Verify successful approval of an ESCALATED exception."""
        from orchestrate.api import approve, ApprovalRequest

        store.save(escalated_exception)
        exc_id = escalated_exception.exception_id

        result = await approve(
            exc_id,
            ApprovalRequest(approved_by="john@acme.com", notes="OK, price increase is known"),
            store,
            fake_redis,
            audit_logger,
        )

        assert result.exception_id == exc_id
        assert result.status == "approved"
        assert "john@acme.com" in result.message

        loaded_exc = store.load(exc_id)
        assert loaded_exc.state == ExceptionState.APPROVED
        assert loaded_exc.approved_by == "john@acme.com"
        assert loaded_exc.approval_notes == "OK, price increase is known"
        assert loaded_exc.approval_timestamp is not None

    @pytest.mark.asyncio
    async def test_approve_without_notes(self, store, fake_redis, audit_logger, escalated_exception):
        """Verify approval works without notes."""
        from orchestrate.api import approve, ApprovalRequest

        store.save(escalated_exception)
        exc_id = escalated_exception.exception_id

        result = await approve(
            exc_id,
            ApprovalRequest(approved_by="jane@acme.com"),
            store,
            fake_redis,
            audit_logger,
        )

        assert result.status == "approved"
        loaded_exc = store.load(exc_id)
        assert loaded_exc.approved_by == "jane@acme.com"
        assert loaded_exc.approval_notes is None

    @pytest.mark.asyncio
    async def test_cannot_approve_received_exception(self, store, fake_redis, audit_logger, sample_po, sample_invoice):
        """Verify cannot approve an exception not in ESCALATED or PENDING_APPROVAL state."""
        from fastapi import HTTPException
        from orchestrate.api import approve, ApprovalRequest

        exc = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=None,
            state=ExceptionState.RECEIVED,
        )
        store.save(exc)

        with pytest.raises(HTTPException) as exc_info:
            await approve(
                exc.exception_id,
                ApprovalRequest(approved_by="john@acme.com"),
                store,
                fake_redis,
                audit_logger,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_pending_approval_exception(self, store, fake_redis, audit_logger, sample_po, sample_invoice):
        """Verify approval of PENDING_APPROVAL exception works."""
        from orchestrate.api import approve, ApprovalRequest

        exc = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=None,
            state=ExceptionState.PENDING_APPROVAL,
        )
        store.save(exc)

        result = await approve(
            exc.exception_id,
            ApprovalRequest(approved_by="john@acme.com"),
            store,
            fake_redis,
            audit_logger,
        )

        assert result.status == "approved"
        assert store.load(exc.exception_id).state == ExceptionState.APPROVED


class TestRejectEndpoint:
    """Tests for POST /tools/reject/{exception_id}"""

    @pytest.mark.asyncio
    async def test_reject_escalated_exception(self, store, fake_redis, audit_logger, escalated_exception):
        """Verify successful rejection of an ESCALATED exception."""
        from orchestrate.api import reject, RejectionRequest

        store.save(escalated_exception)
        exc_id = escalated_exception.exception_id

        result = await reject(
            exc_id,
            RejectionRequest(
                rejected_by="jane@acme.com",
                reason="Price increase not authorized",
            ),
            store,
            fake_redis,
            audit_logger,
        )

        assert result.exception_id == exc_id
        assert result.status == "rejected"
        assert "jane@acme.com" in result.message

        loaded_exc = store.load(exc_id)
        assert loaded_exc.state == ExceptionState.REJECTED
        assert loaded_exc.rejected_by == "jane@acme.com"
        assert loaded_exc.rejection_reason == "Price increase not authorized"
        assert loaded_exc.rejection_timestamp is not None

    @pytest.mark.asyncio
    async def test_cannot_reject_resolved_exception(self, store, fake_redis, audit_logger, sample_po, sample_invoice):
        """Verify cannot reject an exception already RESOLVED."""
        from fastapi import HTTPException
        from orchestrate.api import reject, RejectionRequest

        exc = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=None,
            state=ExceptionState.RESOLVED,
        )
        store.save(exc)

        with pytest.raises(HTTPException) as exc_info:
            await reject(
                exc.exception_id,
                RejectionRequest(rejected_by="jane@acme.com", reason="Test"),
                store,
                fake_redis,
                audit_logger,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reject_pending_approval_exception(self, store, fake_redis, audit_logger, sample_po, sample_invoice):
        """Verify rejection of PENDING_APPROVAL exception works."""
        from orchestrate.api import reject, RejectionRequest

        exc = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=None,
            state=ExceptionState.PENDING_APPROVAL,
        )
        store.save(exc)

        result = await reject(
            exc.exception_id,
            RejectionRequest(rejected_by="jane@acme.com", reason="Not approved"),
            store,
            fake_redis,
            audit_logger,
        )

        assert result.status == "rejected"
        assert store.load(exc.exception_id).state == ExceptionState.REJECTED


class TestListExceptionsEndpoint:
    """Tests for POST /exceptions/list"""

    @pytest.mark.asyncio
    async def test_list_all_exceptions(self, store, fake_redis, sample_po, sample_invoice, sample_grn):
        """Verify listing all exceptions without filters."""
        from orchestrate.api import list_exceptions, ExceptionListRequest

        for i in range(3):
            exc = InvoiceException(
                invoice=sample_invoice,
                purchase_order=sample_po,
                grn=sample_grn,
                state=ExceptionState.ESCALATED,
            )
            store.save(exc)

        result = await list_exceptions(
            ExceptionListRequest(),
            store,
            fake_redis,
        )

        assert result.total_count == 3
        assert len(result.exceptions) == 3
        assert all(e.state == "escalated" for e in result.exceptions)

    @pytest.mark.asyncio
    async def test_filter_by_supplier(self, store, fake_redis, sample_po, sample_invoice, sample_grn):
        """Verify filtering by supplier_id."""
        from orchestrate.api import list_exceptions, ExceptionListRequest

        exc1 = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=sample_grn,
            state=ExceptionState.ESCALATED,
        )
        store.save(exc1)

        po2 = PurchaseOrder(
            po_number="PO-002",
            supplier_id="SUP-002",
            supplier_name="Different Supplier",
            created_by="buyer@company.com",
            department="Office Supplies",
            cost_center="CC-100",
            creation_date=date(2024, 3, 1),
            line_items=[
                LineItem(
                    sku="ITEM-001",
                    description="Item",
                    product_grade="Standard",
                    unit_price=100.0,
                    quantity=1,
                    total=100.0,
                )
            ],
            total_amount=100.0,
        )
        invoice2 = Invoice(
            invoice_number="INV-002",
            supplier_id="SUP-002",
            supplier_name="Different Supplier",
            po_number="PO-002",
            invoice_date=date(2024, 3, 15),
            due_date=date(2024, 4, 14),
            payment_terms="Net 30",
            line_items=po2.line_items,
            total_amount=100.0,
        )
        exc2 = InvoiceException(
            invoice=invoice2,
            purchase_order=po2,
            grn=None,
            state=ExceptionState.ESCALATED,
        )
        store.save(exc2)

        result = await list_exceptions(
            ExceptionListRequest(supplier_id="SUP-001"),
            store,
            fake_redis,
        )

        assert result.total_count == 1
        assert result.exceptions[0].supplier_id == "SUP-001"

    @pytest.mark.asyncio
    async def test_filter_by_status(self, store, fake_redis, sample_po, sample_invoice, sample_grn):
        """Verify filtering by status."""
        from orchestrate.api import list_exceptions, ExceptionListRequest

        exc1 = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=sample_grn,
            state=ExceptionState.ESCALATED,
        )
        store.save(exc1)

        exc2 = InvoiceException(
            invoice=sample_invoice,
            purchase_order=sample_po,
            grn=sample_grn,
            state=ExceptionState.APPROVED,
            approved_by="john@acme.com",
        )
        store.save(exc2)

        result = await list_exceptions(
            ExceptionListRequest(status="approved"),
            store,
            fake_redis,
        )

        assert result.total_count == 1
        assert result.exceptions[0].state == "approved"

    @pytest.mark.asyncio
    async def test_pagination(self, store, fake_redis, sample_po, sample_invoice, sample_grn):
        """Verify pagination works correctly."""
        from orchestrate.api import list_exceptions, ExceptionListRequest

        for i in range(10):
            exc = InvoiceException(
                invoice=sample_invoice,
                purchase_order=sample_po,
                grn=sample_grn,
                state=ExceptionState.ESCALATED,
            )
            store.save(exc)

        result1 = await list_exceptions(
            ExceptionListRequest(limit=5, offset=0),
            store,
            fake_redis,
        )

        assert result1.total_count == 10
        assert len(result1.exceptions) == 5
        assert result1.limit == 5
        assert result1.offset == 0

        result2 = await list_exceptions(
            ExceptionListRequest(limit=5, offset=5),
            store,
            fake_redis,
        )

        assert result2.total_count == 10
        assert len(result2.exceptions) == 5
