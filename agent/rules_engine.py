"""
agent/rules_engine.py

Six-step decision engine for invoice exception resolution.

Step  Gate                                    Action if met
────  ──────────────────────────────────────  ─────────────────────
  0   Duplicate invoice                       AUTO_REJECT (1.0)
  1   No exceptions at all                    AUTO_APPROVE (1.0)
  2   PO variance ≤ 1 % of PO total           AUTO_APPROVE (1.0)
  3   Similar historical approved case        AUTO_APPROVE (0.9)
  4   Email / transcript directly confirms   AUTO_APPROVE (0.85)
  5   External web research corroborates      AUTO_APPROVE (0.80)
  6   None of the above                       ESCALATE_TO_HUMAN (0.5)

Steps 3–5 are run inside the individual orchestrate API tools so that
watsonx Orchestrate can display each gate result to the operator.

This module also exposes apply_rules() — the consolidated single-call
entry point used by Tool 6 (resolve) when the incremental checks have
already been stored in Redis.  Pass the pre-computed gate results as
keyword arguments; apply_rules() honours the first gate that resolved.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from agent.comms_checker import CommsCheckResult, check_communications
from agent.context_retriever import SupplierContext
from agent.history_checker import HistoricalCheckResult, check_historical_approval
from agent.researcher import ResearchResult
from config.settings import AppConfig
from models.exception import ExceptionType, InvoiceException
from models.resolution import ResolutionAction, RootCause

logger = logging.getLogger(__name__)

# Minimum average confidence of Tavily evidence to auto-approve via web research
RESEARCH_CORROBORATION_THRESHOLD = 0.7


class RulesDecision(BaseModel):
    """Output of the rules engine for a single exception."""

    action: ResolutionAction
    root_cause: RootCause
    confidence: float
    reasoning: str
    """Plain-English explanation of which gate fired and why."""
    auto_resolvable: bool
    approved_by_step: int = 0
    """Which step (2-5) triggered auto-approval, or 0 for reject/escalate."""
    recommended_po_adjustment: Optional[Decimal] = None
    """Suggested adjustment to align the PO with the invoice, if applicable."""


# ---------------------------------------------------------------------------
# Individual gate functions (also called directly by the orchestrate tools)
# ---------------------------------------------------------------------------


def gate_duplicate(exception: InvoiceException) -> Optional[RulesDecision]:
    """Gate 0: Duplicate invoice → AUTO_REJECT."""
    if ExceptionType.DUPLICATE_INVOICE in exception.exception_types:
        return RulesDecision(
            action=ResolutionAction.AUTO_REJECT,
            root_cause=RootCause.DUPLICATE_SUBMISSION,
            confidence=1.0,
            reasoning="Invoice identified as a duplicate submission based on supplier history.",
            auto_resolvable=True,
            approved_by_step=0,
        )
    return None


def gate_straight_through(exception: InvoiceException) -> Optional[RulesDecision]:
    """Gate 1: No exceptions → AUTO_APPROVE."""
    if not exception.exception_types:
        return RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
            confidence=1.0,
            reasoning="Three-way match passed with no variances detected.",
            auto_resolvable=True,
            approved_by_step=1,
        )
    return None


def gate_tolerance(
    exception: InvoiceException, config: AppConfig
) -> Optional[RulesDecision]:
    """
    Gate 2: Absolute PO variance % is within the configured 1% tolerance.

    This gate intentionally uses the total invoice-vs-PO variance percentage,
    not line-level heuristics, because Step 1 has already identified the
    exception type(s). Small mismatches should be auto-approved regardless of
    whether they originated from price, quantity, or substitution differences.
    """
    variance_pct = _po_variance_pct(exception)
    if variance_pct > config.price_tolerance_pct:
        return None
    return RulesDecision(
        action=ResolutionAction.AUTO_APPROVE,
        root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
        confidence=1.0,
        reasoning=(
            f"Absolute invoice-to-PO variance is {variance_pct:.2%}, which is within the "
            f"configured {config.price_tolerance_pct:.0%} auto-approval threshold."
        ),
        auto_resolvable=True,
        approved_by_step=2,
    )


def gate_history(exception: InvoiceException) -> tuple[Optional[RulesDecision], HistoricalCheckResult]:
    """
    Gate 3: Similar historical approved case exists → AUTO_APPROVE.

    Returns both the decision (or None) and the raw HistoricalCheckResult
    so the orchestrate tool can surface the details to the operator.
    """
    result = check_historical_approval(exception)
    if result.auto_approve:
        assert result.best_match is not None
        m = result.best_match
        decision = RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
            confidence=0.90,
            reasoning=result.reasoning,
            auto_resolvable=True,
            approved_by_step=3,
        )
        return decision, result
    return None, result


def gate_communications(exception: InvoiceException) -> tuple[Optional[RulesDecision], CommsCheckResult]:
    """
    Gate 4: Email or transcript directly confirms exception → AUTO_APPROVE.

    Returns both the decision (or None) and the raw CommsCheckResult.
    """
    result = check_communications(exception)
    if result.auto_approve:
        decision = RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
            confidence=0.85,
            reasoning=result.reasoning,
            auto_resolvable=True,
            approved_by_step=4,
        )
        return decision, result
    return None, result


def gate_research(
    exception: InvoiceException,
    research: ResearchResult,
) -> Optional[RulesDecision]:
    """
    Gate 5: External web research corroborates the exception → AUTO_APPROVE.

    Requires research.supports_informal_modification == True AND the average
    confidence of supporting_evidence items ≥ RESEARCH_CORROBORATION_THRESHOLD.
    """
    if not research.supports_informal_modification:
        return None

    if not research.supporting_evidence:
        return None

    avg_conf = sum(e.confidence for e in research.supporting_evidence) / len(
        research.supporting_evidence
    )
    if avg_conf < RESEARCH_CORROBORATION_THRESHOLD:
        return None

    return RulesDecision(
        action=ResolutionAction.AUTO_APPROVE,
        root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
        confidence=0.80,
        reasoning=(
            f"Web research corroborates this exception: {research.relevance_summary} "
            f"({len(research.supporting_evidence)} supporting source(s), "
            f"avg confidence {avg_conf:.2f})."
        ),
        auto_resolvable=True,
        approved_by_step=5,
    )


def gate_escalate() -> RulesDecision:
    """Gate 6: No gate fired → ESCALATE_TO_HUMAN."""
    return RulesDecision(
        action=ResolutionAction.ESCALATE_TO_HUMAN,
        root_cause=RootCause.UNRESOLVED,
        confidence=0.5,
        reasoning=(
            "None of the automated approval gates resolved this exception: "
            "variance exceeds the 1% tolerance (Gate 2), "
            "no sufficiently similar historical approved case found (Gate 3), "
            "linked communications do not directly confirm the exception (Gate 4), "
            "and external web research did not corroborate it (Gate 5). "
            "Human review required."
        ),
        auto_resolvable=False,
        approved_by_step=0,
    )


# ---------------------------------------------------------------------------
# Consolidated entry point (used by Tool 6 / resolve)
# ---------------------------------------------------------------------------


def apply_rules(
    exception: InvoiceException,
    context: SupplierContext,
    research: ResearchResult,
    config: AppConfig,
    *,
    history_result: Optional[HistoricalCheckResult] = None,
    comms_result: Optional[CommsCheckResult] = None,
) -> RulesDecision:
    """
    Run all gates in order and return the first decision that fires.

    When the orchestrate API has already run gates 3 and 4 separately
    (storing results in Redis), pass in those pre-computed results as
    history_result / comms_result to avoid duplicate work.
    """
    # Gate 0 — duplicate
    d = gate_duplicate(exception)
    if d:
        return d

    # Gate 1 — straight-through
    d = gate_straight_through(exception)
    if d:
        return d

    # Gate 2 — tolerance
    d = gate_tolerance(exception, config)
    if d:
        return d

    # Gate 3 — historical
    if history_result is None:
        d, history_result = gate_history(exception)
        if d:
            return d
    elif history_result.auto_approve:
        return RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
            confidence=0.90,
            reasoning=history_result.reasoning,
            auto_resolvable=True,
            approved_by_step=3,
        )

    # Gate 4 — communications
    if comms_result is None:
        d, comms_result = gate_communications(exception)
        if d:
            return d
    elif comms_result.auto_approve:
        return RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.UNDOCUMENTED_MODIFICATION,
            confidence=0.85,
            reasoning=comms_result.reasoning,
            auto_resolvable=True,
            approved_by_step=4,
        )

    # Gate 5 — web research
    d = gate_research(exception, research)
    if d:
        return d

    # Gate 6 — escalate
    return gate_escalate()


# ---------------------------------------------------------------------------
# Tolerance helper
# ---------------------------------------------------------------------------


def _po_variance_pct(exception: InvoiceException) -> float:
    """Return the absolute invoice-vs-PO variance as a fraction of PO total."""
    po_total = float(exception.purchase_order.total_amount)
    if po_total <= 0:
        return 0.0
    usd_var = abs(
        float(exception.invoice.total_amount) - float(exception.purchase_order.total_amount)
    )
    return usd_var / po_total
