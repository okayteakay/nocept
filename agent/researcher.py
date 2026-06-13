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

    Gracefully handles Tavily API failures and returns empty results if research
    is completely unavailable.
    """
    queries = _build_search_queries(exception, context)
    logger.info(f"Running {len(queries)} Tavily searches for exception {exception.exception_id}")

    all_findings: list[TavilySearchResult] = []

    for query in queries:
        try:
            logger.debug(f"Searching Tavily for: {query}")
            results = tavily.search(query)
            logger.info(f"Tavily query '{query}' returned {len(results)} results")
            all_findings.extend(results)
        except TimeoutError as e:
            logger.warning(f"Tavily search timed out for query '{query}': {e}")
            continue
        except Exception as e:
            logger.warning(f"Tavily search failed for query '{query}': {e}")
            continue

    # De-duplicate by URL/title and sort by relevance
    unique_findings = {f.url or f.title: f for f in all_findings}.values()
    sorted_findings = sorted(unique_findings, key=lambda x: x.score, reverse=True)
    logger.debug(f"Deduplicated to {len(sorted_findings)} unique findings")

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
                        f"Snippet: {f.content[:200] if f.content else 'N/A'}..."
                    ),
                    url=f.url,
                    confidence=rel_score,
                )
            )

    return ResearchResult(
        queries_run=queries,
        findings=sorted_findings[:10],  # Limit to top 10 to avoid bloat
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
    return list(dict.fromkeys(queries))


def _score_relevance(
    result: TavilySearchResult,
    exception: InvoiceException,
) -> float:
    """Compute a relevance score for a Tavily result against the exception.

    Returns conservative 0.0 if any error occurs.
    """
    try:
        base_score = result.score if result.score is not None else 0.0
        title = result.title or ""
        content = result.content or ""
        text = (title + " " + content).lower()

        # Boost if supplier name or SKUs appear in text
        boost = 0.0
        if exception.supplier_name.lower() in text:
            boost += 0.2
        for v in exception.line_variances:
            if v.sku and v.sku.lower() in text:
                boost += 0.3
                break

        return min(1.0, base_score + boost)
    except Exception as e:
        logger.warning(f"Error scoring relevance for result '{result.title}': {e}")
        return 0.0
