from __future__ import annotations

import logging
from decimal import Decimal
from pydantic import BaseModel

from agent.context_retriever import SupplierContext
from agent.researcher import ResearchResult
from config.settings import AppConfig
from models.exception import ExceptionType, InvoiceException
from models.resolution import ResolutionAction, RootCause

logger = logging.getLogger(__name__)

# Minimum research confidence required to auto-resolve an informal modification.
RESEARCH_CORROBORATION_THRESHOLD = 0.7


class RulesDecision(BaseModel):
    """Output of the rules engine for a single exception."""

    action: ResolutionAction
    root_cause: RootCause
    confidence: float
    reasoning: str
    """Plain-English explanation of which rule fired and why."""
    auto_resolvable: bool
    recommended_po_adjustment: Decimal | None = None
    """Suggested adjustment to align the PO with the invoice, if applicable."""


def apply_rules(
    exception: InvoiceException,
    context: SupplierContext,
    research: ResearchResult,
    config: AppConfig,
) -> RulesDecision:
    """Evaluate all business rules against the exception and return a decision."""

    # Rule 1: DUPLICATE -> AUTO_REJECT
    if ExceptionType.DUPLICATE_INVOICE in exception.exception_types:
        return RulesDecision(
            action=ResolutionAction.AUTO_REJECT,
            root_cause=RootCause.DUPLICATE_SUBMISSION,
            confidence=1.0,
            reasoning="Invoice identified as a duplicate submission based on supplier history in Redis.",
            auto_resolvable=True,
        )

    # Rule 2: Within tolerance -> AUTO_APPROVE
    if _within_tolerance(exception, config):
        return RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
            confidence=1.0,
            reasoning="Amount variance is within allowed corporate policy tolerances.",
            auto_resolvable=True,
        )

    # Rule 3: Known substitution pattern + research corroborates -> AUTO_APPROVE
    if _is_known_substitution_pattern(exception, context):
        if research.supports_informal_modification:
            return RulesDecision(
                action=ResolutionAction.AUTO_APPROVE,
                root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
                confidence=0.9,
                reasoning="Matched a known substitution pattern for this supplier and research corroborates the modification.",
                auto_resolvable=True,
            )

    # Rule 4: Research alone corroborates -> AUTO_APPROVE with memo
    if research.supports_informal_modification:
        # check if average confidence of supporting evidence is high enough
        if research.supporting_evidence and (sum(e.confidence for e in research.supporting_evidence) / len(research.supporting_evidence)) >= RESEARCH_CORROBORATION_THRESHOLD:
            return RulesDecision(
                action=ResolutionAction.AUTO_APPROVE,
                root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
                confidence=0.8,
                reasoning="External research highly corroborates an undocumented modification (e.g. price increase or substitution).",
                auto_resolvable=True,
            )

    # Rule 5: Exceeds tolerance with no corroborating evidence -> ESCALATE
    return RulesDecision(
        action=ResolutionAction.ESCALATE_TO_HUMAN,
        root_cause=RootCause.UNRESOLVED,
        confidence=0.5,
        reasoning="Variance exceeds tolerances and no corroborating evidence found in research or history.",
        auto_resolvable=False,
    )


def _within_tolerance(exception: InvoiceException, config: AppConfig) -> bool:
    """Return True if the exception's variance is within the configured tolerances."""
    # Absolute USD variance
    usd_variance = abs(exception.invoice.total_amount - exception.purchase_order.total_amount)
    if usd_variance > config.auto_resolve_max_variance_usd:
        return False

    # Line-level check
    for v in exception.line_variances:
        if v.is_new_sku:
            # If there's a new SKU, it's likely a modification, not just a variance
            return False
        if v.price_delta_pct is not None and abs(v.price_delta_pct) > config.price_tolerance_pct:
            return False
        if v.quantity_delta is not None and v.po_quantity is not None and v.po_quantity > 0:
            if abs(v.quantity_delta) / v.po_quantity > config.qty_tolerance_pct:
                return False

    return True


def _is_known_substitution_pattern(
    exception: InvoiceException,
    context: SupplierContext,
) -> bool:
    """Return True if the exception matches a known substitution pattern in history."""
    for v in exception.line_variances:
        if v.is_new_sku:
            # find if this new SKU was substituted for some shortfall SKU
            shortfalls = [sv for sv in exception.line_variances if not sv.is_new_sku and sv.quantity_delta is not None and sv.quantity_delta < 0]
            for sv in shortfalls:
                # find if this (shortfall_sku, new_sku) pair exists in historical patterns
                for pattern in context.substitution_patterns:
                    if pattern["from_sku"] == sv.sku and pattern["to_sku"] == v.sku:
                        if pattern["count"] >= 2:
                            return True
    return False
