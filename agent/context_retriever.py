from __future__ import annotations

import logging
from decimal import Decimal

from pydantic import BaseModel

from models.exception import ExceptionType, InvoiceException
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class SupplierContext(BaseModel):
    """Historical context for a supplier, retrieved from Redis."""

    supplier_id: str
    historical_exceptions: list[InvoiceException]
    substitution_patterns: list[dict]
    """
    Each entry describes a known SKU substitution pattern, e.g.::

        {
            "from_sku": "PAPER-A-REAM",
            "to_sku": "PAPER-B-REAM",
            "count": 7,
            "avg_price_uplift_pct": 0.067,
        }
    """
    average_price_uplift_pct: float | None
    exception_rate: float | None
    """Ratio of exceptions to total invoices processed for this supplier."""

    model_config = {"arbitrary_types_allowed": True}


def retrieve_supplier_context(
    supplier_id: str,
    store: RedisStateStore,
    lookback_days: int = 180,
) -> SupplierContext:
    """Fetch all historical exceptions for a supplier and summarize patterns.

    Pulls resolved and escalated exceptions for the supplier from Redis,
    filters to the lookback window, and derives substitution pattern summaries
    and average price uplift metrics.

    Args:
        supplier_id: Supplier identifier.
        store: The Redis state store.
        lookback_days: How many calendar days of history to consider.

    Returns:
        SupplierContext with historical exceptions and derived pattern summaries.
    """
    raise NotImplementedError


def _extract_substitution_patterns(
    exceptions: list[InvoiceException],
) -> list[dict]:
    """Derive substitution pattern summaries from a list of exceptions.

    Identifies cases where an invoice contained a SKU not on the PO
    (is_new_sku=True in LineItemVariance) and groups by (from_sku, to_sku) pairs.

    Args:
        exceptions: Historical InvoiceException objects for a supplier.

    Returns:
        List of pattern dicts, each with keys:
        from_sku, to_sku, count, avg_price_uplift_pct.
    """
    raise NotImplementedError


def _compute_average_price_uplift(
    exceptions: list[InvoiceException],
) -> float | None:
    """Compute the average price uplift percentage across informal modification exceptions.

    Only considers exceptions classified as INFORMAL_MODIFICATION.
    Returns None if there are no qualifying exceptions.

    Args:
        exceptions: Historical InvoiceException objects.

    Returns:
        Average price uplift as a float (e.g. 0.067 for 6.7%), or None.
    """
    raise NotImplementedError
