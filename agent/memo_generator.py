from __future__ import annotations

import logging

from agent.context_retriever import SupplierContext
from agent.researcher import ResearchResult
from agent.rules_engine import RulesDecision
from models.exception import InvoiceException
from models.resolution import EvidenceItem, ResolutionMemo

logger = logging.getLogger(__name__)


def generate_memo(
    exception: InvoiceException,
    decision: RulesDecision,
    research: ResearchResult,
    context: SupplierContext,
) -> ResolutionMemo:
    """Assemble the structured resolution memo for an exception.

    Combines the rules engine decision, research findings, and supplier history
    into a ResolutionMemo that serves as the audit record and human-readable
    output for each resolved exception.

    Args:
        exception: The InvoiceException being resolved.
        decision: The RulesDecision from the rules engine.
        research: External research results from Tavily.
        context: Supplier historical context from Redis.

    Returns:
        A fully populated ResolutionMemo.
    """
    raise NotImplementedError


def _format_evidence_items(
    research: ResearchResult,
    context: SupplierContext,
    decision: RulesDecision,
) -> list[EvidenceItem]:
    """Build the list of EvidenceItems for the memo.

    Sources combined in priority order:
    1. Redis history evidence (known substitution patterns → source="redis_history")
    2. Tavily search findings above relevance threshold (source="tavily_search")
    3. Rule engine rationale (source="rule_engine")

    Args:
        research: Tavily research results.
        context: Supplier context with historical patterns.
        decision: Rules decision with reasoning.

    Returns:
        Ordered list of EvidenceItem objects.
    """
    raise NotImplementedError


def _write_summary(
    exception: InvoiceException,
    decision: RulesDecision,
    context: SupplierContext,
) -> str:
    """Write the plain-English summary section of the memo.

    The summary should be 2–4 sentences covering:
    - What the exception is (mismatch type and amounts)
    - The root cause determination
    - The recommended action
    - Any relevant historical pattern context

    Args:
        exception: The InvoiceException.
        decision: The RulesDecision.
        context: Supplier context.

    Returns:
        A multi-sentence summary string.
    """
    raise NotImplementedError
