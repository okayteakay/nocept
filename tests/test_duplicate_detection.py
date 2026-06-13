"""Tests for the (supplier_id, invoice_number, total_amount) duplicate-detection
fingerprint in agent/classifier.py::check_duplicate.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from agent.classifier import check_duplicate
from models.exception import ExceptionState, InvoiceException
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder
from models.grn import GoodsReceiptNote


def _make_invoice(
    supplier_id: str = "SUP-001",
    supplier_name: str = "Test Supplier",
    invoice_number: str = "INV-001",
    total_amount: float = 1000.0,
) -> Invoice:
    return Invoice(
        invoice_number=invoice_number,
        po_number="PO-001",
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=[
            LineItem(
                sku="SKU-1",
                description="Test Item",
                product_grade="Standard",
                unit_price=10.0,
                quantity=100,
                total=1000.0,
            )
        ],
        total_amount=total_amount,
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        payment_terms="Net 30",
    )


def _make_exception(invoice: Invoice) -> InvoiceException:
    po = PurchaseOrder(
        po_number="PO-001",
        supplier_id=invoice.supplier_id,
        supplier_name=invoice.supplier_name,
        line_items=invoice.line_items,
        total_amount=invoice.total_amount,
        creation_date=date(2026, 1, 1),
        created_by="buyer@example.com",
        department="Procurement",
        cost_center="CC-1",
    )
    grn = GoodsReceiptNote(
        gr_number="GR-001",
        po_number="PO-001",
        invoice_number=invoice.invoice_number,
        supplier_id=invoice.supplier_id,
        line_items=invoice.line_items,
        date_received=date(2026, 1, 2),
        received_by="warehouse@example.com",
    )
    return InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RESOLVED,
    )


class TestCheckDuplicate:
    def test_same_supplier_same_invoice_same_amount_is_duplicate(self, fake_redis):
        from state.redis_backend import RedisStateStore
        store = RedisStateStore(fake_redis)
        original = _make_exception(_make_invoice())
        store.save(original)

        new_invoice = _make_invoice()  # identical supplier/number/amount
        assert check_duplicate(new_invoice, store) is True

    def test_same_supplier_same_invoice_different_amount_not_duplicate(self, fake_redis):
        from state.redis_backend import RedisStateStore
        store = RedisStateStore(fake_redis)
        original = _make_exception(_make_invoice(total_amount=1000.0))
        store.save(original)

        # Same supplier, same invoice number, but amount differs by > $0.01
        new_invoice = _make_invoice(total_amount=1500.0)
        assert check_duplicate(new_invoice, store) is False

    def test_different_supplier_same_invoice_number_not_duplicate(self, fake_redis):
        from state.redis_backend import RedisStateStore
        store = RedisStateStore(fake_redis)
        original = _make_exception(_make_invoice(supplier_id="SUP-001"))
        store.save(original)

        new_invoice = _make_invoice(supplier_id="SUP-002")
        assert check_duplicate(new_invoice, store) is False

    def test_no_prior_exceptions_returns_false(self, fake_redis):
        from state.redis_backend import RedisStateStore
        store = RedisStateStore(fake_redis)
        new_invoice = _make_invoice()
        assert check_duplicate(new_invoice, store) is False

    def test_redis_failure_fails_open(self, fake_redis):
        from state.redis_backend import RedisStateStore
        # Force an error: pass a store that throws on list_by_supplier
        class BrokenStore:
            def list_by_supplier(self, supplier_id):
                raise ConnectionError("redis down")

        new_invoice = _make_invoice()
        # Should not raise — fail open
        assert check_duplicate(new_invoice, BrokenStore()) is False
