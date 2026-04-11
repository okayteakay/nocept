"""Tests for ingestion layer — CSV parsing and ERP simulator."""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from ingestion.csv_ingestor import IngestValidationError, ingest_from_csv
from ingestion.erp_simulator import (
    generate_batch,
    generate_informal_modification_exception,
    generate_missing_receipt_exception,
    generate_price_variance_exception,
    generate_straight_through_invoice,
    generate_duplicate_exception,
)
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_temp_csv(rows: list[dict], path: Path) -> str:
    """Write a list of row dicts to a temp CSV and return the path."""
    if not rows:
        return str(path)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


# ---------------------------------------------------------------------------
# CSV Ingestor tests
# ---------------------------------------------------------------------------

class TestCSVIngestor:
    def test_csv_ingest_parses_single_invoice(self, tmp_path):
        """A valid single-line-item invoice CSV should produce one Invoice object."""
        ...

    def test_csv_ingest_matches_invoice_to_po(self, tmp_path):
        """Invoices should be matched to POs via po_number."""
        ...

    def test_csv_ingest_matches_grn_when_provided(self, tmp_path):
        """GRNs should be matched by po_number and returned with the correct invoice."""
        ...

    def test_csv_ingest_returns_none_grn_when_no_grn_file(self, tmp_path):
        """When grn_path is None, all tuples should have grn=None."""
        ...

    def test_csv_ingest_groups_multi_line_invoice(self, tmp_path):
        """Multiple rows sharing an invoice_id should produce a single Invoice with multiple LineItems."""
        ...

    def test_csv_schema_mismatch_raises_ingest_validation_error(self, tmp_path):
        """A CSV missing required columns should raise IngestValidationError."""
        ...

    def test_missing_invoice_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ingest_from_csv("/nonexistent/invoices.csv", "/nonexistent/pos.csv")


# ---------------------------------------------------------------------------
# ERP Simulator tests
# ---------------------------------------------------------------------------

class TestERPSimulator:
    def test_straight_through_returns_valid_objects(self):
        invoice, po, grn = generate_straight_through_invoice()
        assert isinstance(invoice, Invoice)
        assert isinstance(po, PurchaseOrder)
        assert isinstance(grn, GoodsReceiptNote)

    def test_straight_through_invoice_matches_po(self):
        invoice, po, grn = generate_straight_through_invoice()
        assert invoice.total_amount == po.total_amount

    def test_informal_modification_invoice_total_exceeds_po(self):
        invoice, po, grn = generate_informal_modification_exception()
        assert invoice.total_amount > po.total_amount

    def test_informal_modification_invoice_has_two_skus(self):
        invoice, po, grn = generate_informal_modification_exception()
        assert len(invoice.line_items) == 2

    def test_informal_modification_po_has_one_sku(self):
        invoice, po, grn = generate_informal_modification_exception()
        assert len(po.line_items) == 1

    def test_missing_receipt_returns_none_grn(self):
        invoice, po, grn = generate_missing_receipt_exception()
        assert grn is None

    def test_price_variance_invoice_higher_than_po(self):
        invoice, po, grn = generate_price_variance_exception(variance_pct=0.08)
        assert invoice.total_amount > po.total_amount

    def test_duplicate_has_different_invoice_id(self):
        invoice, po, grn = generate_straight_through_invoice()
        duplicate = generate_duplicate_exception(invoice)
        assert duplicate.invoice_id != invoice.invoice_id

    def test_duplicate_has_same_po_number(self):
        invoice, po, grn = generate_straight_through_invoice()
        duplicate = generate_duplicate_exception(invoice)
        assert duplicate.po_number == invoice.po_number

    def test_generate_batch_returns_correct_count(self):
        batch = generate_batch(10)
        assert len(batch) == 10

    def test_generate_batch_all_tuples_valid(self):
        batch = generate_batch(5, exception_rate=0.0)
        for invoice, po, grn in batch:
            assert isinstance(invoice, Invoice)
            assert isinstance(po, PurchaseOrder)
