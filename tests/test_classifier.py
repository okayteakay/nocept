"""Tests for agent.classifier — three-way match and exception classification."""
from __future__ import annotations

import pytest

from agent.classifier import ClassificationResult, classify_exception
from models.exception import ExceptionType


class TestStraightThrough:
    def test_no_exception_types_when_documents_match(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        """A perfectly matching invoice/PO/GRN should produce no exception types."""
        ...

    def test_total_variance_zero_when_documents_match(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        ...


class TestPriceVariance:
    def test_price_variance_detected(
        self, price_variance_invoice, price_variance_po, app_config
    ):
        """An 8% price uplift above the 5% threshold should be flagged as PRICE_VARIANCE."""
        ...

    def test_price_variance_within_tolerance_not_flagged(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        """A 2% price uplift below the configured tolerance should not be flagged."""
        ...

    def test_price_delta_pct_computed_correctly(
        self, price_variance_invoice, price_variance_po, app_config
    ):
        ...


class TestQuantityVariance:
    def test_qty_variance_detected_when_invoice_short(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        ...

    def test_qty_variance_not_flagged_within_tolerance(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        ...


class TestMissingReceipt:
    def test_missing_receipt_flagged_when_grn_none(
        self, sample_invoice, sample_po, app_config
    ):
        ...

    def test_no_missing_receipt_when_grn_present(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        ...


class TestInformalModification:
    def test_informal_modification_signals_detected(
        self, informal_mod_invoice, informal_mod_po, informal_mod_grn, app_config
    ):
        """The canonical Grade A → Grade B substitution should trigger INFORMAL_MODIFICATION."""
        ...

    def test_new_sku_flagged_as_informal_modification_signal(
        self, informal_mod_invoice, informal_mod_po, informal_mod_grn, app_config
    ):
        ...

    def test_informal_modification_signals_list_non_empty(
        self, informal_mod_invoice, informal_mod_po, informal_mod_grn, app_config
    ):
        ...

    def test_both_price_and_informal_flags_can_coexist(
        self, informal_mod_invoice, informal_mod_po, informal_mod_grn, app_config
    ):
        ...


class TestDuplicate:
    def test_duplicate_detected_via_store(
        self, sample_invoice, sample_po, sample_grn, store, app_config
    ):
        """Second submission of same invoice_id should be flagged as DUPLICATE."""
        ...

    def test_no_duplicate_without_store(
        self, sample_invoice, sample_po, sample_grn, app_config
    ):
        """Without a store, duplicate detection is skipped gracefully."""
        ...
