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
    """Assemble the structured resolution memo for an exception."""

    evidence = _format_evidence_items(research, context, decision, exception)
    summary = _write_summary(exception, decision, context)

    return ResolutionMemo(
        exception_id=exception.exception_id,
        root_cause=decision.root_cause,
        action=decision.action,
        confidence=decision.confidence,
        summary=summary,
        evidence=evidence,
        recommended_po_adjustment=decision.recommended_po_adjustment,
    )


def _format_evidence_items(
    research: ResearchResult,
    context: SupplierContext,
    decision: RulesDecision,
    exception: InvoiceException,
) -> list[EvidenceItem]:
    """Build the list of EvidenceItems for the memo."""
    evidence_list: list[EvidenceItem] = []

    # 1. Redis history evidence
    for pattern in context.substitution_patterns:
        # Only add if it was actually used in the decision
        if any([p["to_sku"] == v.sku for v in exception.line_variances if v.is_new_sku for p in context.substitution_patterns]):
             # This is wrong logic, I'll fix it in the a loop
             pass

    # Revised: Just add relevant patterns found in context
    for pattern in context.substitution_patterns:
        # We'll just check if the to_sku is in the current exception
        # (since we're using the list as an evidence source)
        current_new_skus = [v.sku for v in exception.line_variances if v.is_new_sku]
        if pattern["to_sku"] in current_new_skus:
            evidence_list.append(
                EvidenceItem(
                    source="redis_history",
                    description=f"Matched known substitution pattern: {pattern['from_sku']} -> {pattern['to_sku']} (seen {pattern['count']} times)",
                    confidence=0.9,
                )
            )

    # 2. Tavily search findings
    for item in research.supporting_evidence:
        evidence_list.append(item)

    # 3. Rule engine rationale
    evidence_list.append(
        EvidenceItem(
            source="rule_engine",
            description=decision.reasoning,
            confidence=1.0,
        )
    )

    return evidence_list


def _write_summary(
    exception: InvoiceException,
    decision: RulesDecision,
    context: SupplierContext,
) -> str:
    """Write the plain-English summary section of the memo."""

    mismatch_type = ", ".join([t.value for t in exception.exception_types])
    variance_usd = exception.total_variance_usd

    root_cause = decision.root_cause.value.replace("_", " ").title()
    action = decision.action.value.replace("_", " ").title()

    summary = (
        f"The invoice for {exception.supplier_name} contains {mismatch_type} "
        f"with a total variance of ${variance_usd:,.2f}. "
        f"The determined root cause is {root_cause} and the recommended action is {action}. "
    )

    if context.substitution_patterns:
         summary += f"Historical data indicates known substitution patterns for this supplier. "

    return summary
