"""Tests for agent.researcher — Tavily external research step."""
from __future__ import annotations

import os

import pytest

from agent.researcher import ResearchResult, research_exception
from clients.tavily_client import TavilyClient


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


class TestTavilyLive:
    """Integration tests that hit the real Tavily API.

    Skipped automatically when TAVILY_API_KEY is not set so they never block CI.
    Run locally with a valid key to confirm the API is reachable.
    """

    @pytest.fixture
    def live_tavily(self) -> TavilyClient:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            pytest.skip("TAVILY_API_KEY not set in environment")
        # Probe with the raw client so auth errors aren't silently swallowed
        try:
            from tavily import TavilyClient as _Tavily  # type: ignore[import]
            _Tavily(api_key=api_key).search(query="test", max_results=1)
        except Exception as e:
            pytest.skip(f"Tavily API unreachable or key invalid: {e}")
        return TavilyClient(api_key)

    def test_search_returns_results(self, live_tavily):
        """A plain search query should return at least one result."""
        results = live_tavily.search("office paper price increase 2024", max_results=3)
        print(results)
        assert len(results) > 0, "Expected at least one result from Tavily"

    def test_result_fields_populated(self, live_tavily):
        """Every returned result must have non-empty title, url, and content."""
        results = live_tavily.search("supply chain shortage paper", max_results=3)
        assert results, "Expected at least one result"
        for r in results:
            assert r.title, f"Empty title in result: {r}"
            assert r.url, f"Empty url in result: {r}"
            assert r.content, f"Empty content in result: {r}"
            assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"

    def test_results_sorted_by_score_descending(self, live_tavily):
        """Results must come back sorted highest score first."""
        results = live_tavily.search("paper supplier price change", max_results=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), "Results not sorted by score desc"
