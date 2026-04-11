"""Tests for agent.researcher — Tavily external research step."""
from __future__ import annotations

import pytest

from agent.researcher import ResearchResult, research_exception


class TestQueryBuilder:
    def test_queries_include_supplier_name(
        self, informal_mod_triple, mock_tavily, store
    ):
        """Supplier name should appear in at least one generated query."""
        ...

    def test_queries_include_affected_sku(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...

    def test_queries_non_empty(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...

    def test_multiple_queries_generated_for_informal_modification(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...


class TestRelevanceScoring:
    def test_high_score_result_becomes_supporting_evidence(
        self, informal_mod_triple, tavily_with_results, store
    ):
        """A Tavily result with score 0.92 should appear in supporting_evidence."""
        ...

    def test_low_score_result_excluded_from_evidence(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...

    def test_evidence_confidence_between_0_and_1(
        self, informal_mod_triple, tavily_with_results, store
    ):
        ...


class TestNoResults:
    def test_empty_findings_handled_gracefully(
        self, informal_mod_triple, mock_tavily, store
    ):
        """Zero Tavily results should return a valid ResearchResult, not raise."""
        ...

    def test_no_results_sets_supports_informal_false(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...

    def test_no_results_has_empty_evidence(
        self, informal_mod_triple, mock_tavily, store
    ):
        ...


class TestWithCorroboratingEvidence:
    def test_corroborating_result_sets_supports_informal_true(
        self, informal_mod_triple, tavily_with_results, store
    ):
        ...

    def test_relevance_summary_non_empty_with_results(
        self, informal_mod_triple, tavily_with_results, store
    ):
        ...
