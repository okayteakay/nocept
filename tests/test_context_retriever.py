"""Tests for agent.context_retriever — supplier history and pattern extraction."""
from __future__ import annotations

import pytest

from agent.context_retriever import SupplierContext, retrieve_supplier_context


class TestEmptyHistory:
    def test_empty_history_returns_valid_context(self, store, app_config):
        """A supplier with no history should return a valid SupplierContext with empty lists."""
        ...

    def test_empty_history_has_none_average_uplift(self, store, app_config):
        ...

    def test_empty_history_has_none_exception_rate(self, store, app_config):
        ...


class TestSubstitutionPatternExtraction:
    def test_substitution_pattern_extracted_from_history(self, store):
        """Multiple informal modification exceptions with same SKU pair should produce a pattern."""
        ...

    def test_pattern_count_matches_occurrence_count(self, store):
        ...

    def test_pattern_contains_from_and_to_sku(self, store):
        ...

    def test_patterns_deduplicated_by_sku_pair(self, store):
        ...


class TestPriceUpliftComputation:
    def test_price_uplift_computed_from_informal_exceptions(self, store):
        ...

    def test_price_uplift_none_when_no_informal_exceptions(self, store):
        ...

    def test_price_uplift_averaged_across_multiple_exceptions(self, store):
        ...


class TestLookbackWindow:
    def test_exceptions_outside_lookback_excluded(self, store):
        """Exceptions older than lookback_days should not appear in the context."""
        ...

    def test_exceptions_within_lookback_included(self, store):
        ...
