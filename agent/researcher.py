from __future__ import annotations

import logging

from pydantic import BaseModel

from agent.context_retriever import SupplierContext
from clients.tavily_client import TavilyClient, TavilySearchResult
from models.exception import InvoiceException
from models.resolution import EvidenceItem

logger = logging.getLogger(__name__)


class ResearchResult(BaseModel):
    """Output of the external research step."""

    queries_run: list[str]
    findings: list[TavilySearchResult]
    relevance_summary: str
    """Plain-English summary of what the research found."""
    supports_informal_modification: bool
    """True if at least one finding provides corroborating evidence for an informal modification."""
    supporting_evidence: list[EvidenceItem]
    """Evidence items derived from high-relevance findings."""


def research_exception(
    exception: InvoiceException,
    context: SupplierContext,
    tavily: TavilyClient,
) -> ResearchResult:
    """Run targeted external research to explain the exception's root cause.

    Builds search queries from the exception's characteristics (supplier name,
    affected SKUs, variance type) and the supplier's historical context.
    Searches for supplier announcements, supply disruptions, price amendments,
    and product substitution notices. Scores each result for relevance and
    assembles EvidenceItem objects from high-scoring findings.

    Args:
        exception: The InvoiceException being investigated.
        context: Supplier historical context from Redis.
        tavily: TavilyClient for executing searches.

    Returns:
        ResearchResult with all findings and derived evidence.
    """
    raise NotImplementedError


def _build_search_queries(
    exception: InvoiceException,
    context: SupplierContext,
) -> list[str]:
    """Construct targeted search queries from exception characteristics.

    Query strategies:
    - Supplier name + "price increase" / "stock shortage" / "product substitution"
    - Affected SKU description + "discontinued" / "unavailable"
    - Supplier name + product category + current year
    - If informal modification suspected: supplier name + "grade B" / "substitute"

    Args:
        exception: The exception being researched.
        context: Supplier context for additional signals.

    Returns:
        List of query strings to pass to Tavily.
    """
    raise NotImplementedError


def _score_relevance(
    result: TavilySearchResult,
    exception: InvoiceException,
) -> float:
    """Compute a relevance score for a Tavily result against the exception.

    Combines Tavily's own score with keyword matching against the exception's
    supplier name, SKUs, and variance types.

    Args:
        result: A single Tavily search result.
        exception: The exception being researched.

    Returns:
        A float in [0.0, 1.0] representing adjusted relevance.
    """
    raise NotImplementedError


def _summarize_findings(
    findings: list[TavilySearchResult],
    exception: InvoiceException,
) -> str:
    """Generate a plain-English summary of what the research found.

    Args:
        findings: All Tavily results collected.
        exception: The exception being researched.

    Returns:
        A concise summary string for inclusion in the resolution memo.
    """
    raise NotImplementedError
