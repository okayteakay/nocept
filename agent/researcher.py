from __future__ import annotations

import logging
from datetime import datetime

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
    """Run targeted external research to explain the exception's root cause."""
    queries = _build_search_queries(exception, context)
    all_findings: list[TavilySearchResult] = []

    for query in queries:
        try:
            results = tavily.search(query)
            all_findings.extend(results)
        except Exception as e:
            logger.error("Tavily search failed for query %s: %s", query, e)

    # De-duplicate by URL and sort by relevance
    unique_findings = {f.url or f.title: f for f in all_findings}.values()
    sorted_findings = sorted(unique_findings, key=lambda x: x.score, reverse=True)

    # Filter high-relevance findings to build evidence
    evidence = []
    supports_mod = False
    for f in sorted_findings:
        rel_score = _score_relevance(f, exception)
        if rel_score >= 0.7:
            supports_mod = True
            evidence.append(
                EvidenceItem(
                    source="tavily_search",
                    description=(
                        f"Found corroborating info: {f.title}. "
                        f"Snippet: {f.content[:200]}..."
                    ),
                    url=f.url,
                    confidence=rel_score,
                )
            )

    summary = _summarize_findings(sorted_findings, exception)

    return ResearchResult(
        queries_run=queries,
        findings=sorted_findings,
        relevance_summary=summary,
        supports_informal_modification=supports_mod,
        supporting_evidence=evidence,
    )


def _build_search_queries(
    exception: InvoiceException,
    context: SupplierContext,
) -> list[str]:
    """Construct targeted search queries from exception characteristics."""
    supplier = exception.supplier_name
    year = datetime.now().year
    queries = [
        f"{supplier} price increase {year}",
        f"{supplier} price increase {year - 1}",
    ]

    # Product-specific queries from line variances
    for v in exception.line_variances:
        desc = v.description
        if v.price_delta_pct is not None and abs(v.price_delta_pct) > 0.02:
            queries.append(f"{supplier} {desc} price increase")
        if v.is_new_sku:
            queries.append(f"{desc} discontinued unavailable substitution")

    # Generic fallback
    queries.append(f"{supplier} stock shortage product substitution")

    # Context-aware queries
    if context.average_price_uplift_pct and context.average_price_uplift_pct > 0:
        queries.append(
            f"{supplier} announce price uplift {context.average_price_uplift_pct:.1%}"
        )

    # Preserve order while deduplicating.
    deduped = list(dict.fromkeys(queries))
    return deduped


def _score_relevance(
    result: TavilySearchResult,
    exception: InvoiceException,
) -> float:
    """Compute a relevance score for a Tavily result against the exception."""
    base_score = result.score
    text = (result.title + " " + result.content).lower()

    # Boost if supplier name or SKUs appear in text
    boost = 0.0
    if exception.supplier_name.lower() in text:
        boost += 0.2

    for v in exception.line_variances:
        if v.sku.lower() in text:
            boost += 0.3
            break

    return min(1.0, base_score + boost)


def _summarize_findings(
    findings: list[TavilySearchResult],
    exception: InvoiceException,
) -> str:
    """Generate a plain-English summary of what the research found."""
    if not findings:
        return "No external information found regarding this exception."

    top_finding = findings[0]
    if top_finding.score > 0.7:
        return (
            f"Research found a highly relevant match: {top_finding.title}. "
            "This suggests a plausible explanation for the variance."
        )

    return "Research returned several low-relevance results; no strong external corroboration found."
