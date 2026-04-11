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
    recommended_po_adjustment: Decimal | None
    """Suggested adjustment to align the PO with the invoice, if applicable."""


def apply_rules(
    exception: InvoiceException,
    context: SupplierContext,
    research: ResearchResult,
    config: AppConfig,
) -> RulesDecision:
    """Evaluate all business rules against the exception and return a decision.

    Rules are evaluated in priority order. The first rule that matches fires
    and determines the action. Rules:

    1. DUPLICATE → AUTO_REJECT (DUPLICATE_SUBMISSION)
    2. Within price AND qty tolerance → AUTO_APPROVE (POLICY_COMPLIANT_VARIANCE)
    3. Known substitution pattern in Redis history AND research corroborates
       → AUTO_APPROVE (UNDOCUMENTED_MODIFICATION)
    4. Research alone corroborates informal modification above threshold
       → AUTO_APPROVE with memo (UNDOCUMENTED_MODIFICATION)
    5. Exceeds tolerance with no corroborating evidence
       → ESCALATE_TO_HUMAN (UNRESOLVED)

    Args:
        exception: The classified InvoiceException.
        context: Supplier historical context.
        research: External research results.
        config: AppConfig for tolerance thresholds.

    Returns:
        RulesDecision describing the recommended action and rationale.
    """
    raise NotImplementedError


def _within_tolerance(exception: InvoiceException, config: AppConfig) -> bool:
    """Return True if the exception's variance is within the configured tolerances.

    Checks both the absolute USD variance (against auto_resolve_max_variance_usd)
    and the per-line price/quantity percentages.

    Args:
        exception: The exception to evaluate.
        config: AppConfig with tolerance settings.

    Returns:
        True if all variances are within tolerance.
    """
    raise NotImplementedError


def _is_known_substitution_pattern(
    exception: InvoiceException,
    context: SupplierContext,
) -> bool:
    """Return True if the exception matches a known substitution pattern in history.

    Matches on (from_sku, to_sku) pairs in context.substitution_patterns,
    requiring a minimum occurrence count of 2 before treating it as "known".

    Args:
        exception: The exception to evaluate.
        context: Supplier context with historical substitution patterns.

    Returns:
        True if at least one line variance matches a known pattern.
    """
    raise NotImplementedError


def _research_corroborates(
    research: ResearchResult,
    threshold: float = RESEARCH_CORROBORATION_THRESHOLD,
) -> bool:
    """Return True if research findings support auto-resolution.

    Uses research.supports_informal_modification combined with the average
    confidence of supporting_evidence items.

    Args:
        research: Research results from Tavily.
        threshold: Minimum average confidence required.

    Returns:
        True if research corroborates informal modification above threshold.
    """
    raise NotImplementedError
