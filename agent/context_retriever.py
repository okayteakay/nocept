from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from pydantic import BaseModel

from models.exception import ExceptionType, ExceptionState, InvoiceException
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

    Handles Redis connection errors gracefully by returning empty context.
    """
    try:
        # Get all exceptions for the supplier
        exceptions = store.list_by_supplier(supplier_id)
        logger.debug(f"Retrieved {len(exceptions)} total exceptions for supplier {supplier_id}")
    except Exception as e:
        logger.error(f"Failed to retrieve exceptions for supplier {supplier_id}: {e}", exc_info=True)
        # Graceful fallback: return empty context
        return SupplierContext(
            supplier_id=supplier_id,
            historical_exceptions=[],
            substitution_patterns=[],
            average_price_uplift_pct=None,
            exception_rate=None,
        )

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        recent_exceptions = [
            e for e in exceptions
            if e.updated_at >= cutoff and e.state in (ExceptionState.RESOLVED, ExceptionState.ESCALATED)
        ]
        logger.debug(f"Filtered to {len(recent_exceptions)} recent exceptions within {lookback_days} days")
    except Exception as e:
        logger.error(f"Error filtering exceptions for supplier {supplier_id}: {e}", exc_info=True)
        recent_exceptions = []

    try:
        substitution_patterns = _extract_substitution_patterns(recent_exceptions)
        logger.debug(f"Extracted {len(substitution_patterns)} substitution patterns for supplier {supplier_id}")
    except Exception as e:
        logger.error(f"Error extracting substitution patterns for supplier {supplier_id}: {e}", exc_info=True)
        substitution_patterns = []

    try:
        avg_uplift = _compute_average_price_uplift(recent_exceptions)
        if avg_uplift is not None:
            logger.debug(f"Average price uplift for supplier {supplier_id}: {avg_uplift:.2%}")
    except Exception as e:
        logger.error(f"Error computing average price uplift for supplier {supplier_id}: {e}", exc_info=True)
        avg_uplift = None

    # Approximate exception rate if we have enough data
    exception_rate = None
    try:
        if len(exceptions) > 0:
            exception_rate = len(recent_exceptions) / len(exceptions)
            logger.debug(f"Exception rate for supplier {supplier_id}: {exception_rate:.2%}")
    except Exception as e:
        logger.error(f"Error computing exception rate for supplier {supplier_id}: {e}", exc_info=True)
        exception_rate = None

    return SupplierContext(
        supplier_id=supplier_id,
        historical_exceptions=recent_exceptions,
        substitution_patterns=substitution_patterns,
        average_price_uplift_pct=avg_uplift,
        exception_rate=exception_rate,
    )


def _extract_substitution_patterns(
    exceptions: list[InvoiceException],
) -> list[dict]:
    """Derive substitution pattern summaries from a list of exceptions."""
    try:
        patterns: dict[tuple[str, str], list[float]] = {}

        for exc in exceptions:
            try:
                if ExceptionType.INFORMAL_MODIFICATION not in exc.exception_types:
                    continue

                new_skus = [v for v in exc.line_variances if v.is_new_sku]
                shortfalls = [v for v in exc.line_variances if not v.is_new_sku and v.quantity_delta is not None and v.quantity_delta < 0]

                for n_sku in new_skus:
                    for s_sku in shortfalls:
                        pair = (s_sku.sku, n_sku.sku)
                        uplift = n_sku.price_delta_pct or 0.0
                        patterns.setdefault(pair, []).append(uplift)
            except Exception as e:
                logger.warning(f"Error processing exception {getattr(exc, 'exception_id', 'unknown')}: {e}")
                continue

        result = []
        for (from_sku, to_sku), uplifts in patterns.items():
            try:
                if uplifts:
                    result.append({
                        "from_sku": from_sku,
                        "to_sku": to_sku,
                        "count": len(uplifts),
                        "avg_price_uplift_pct": sum(uplifts) / len(uplifts),
                    })
            except Exception as e:
                logger.warning(f"Error aggregating pattern ({from_sku}, {to_sku}): {e}")
                continue

        return result
    except Exception as e:
        logger.error(f"Error extracting substitution patterns: {e}", exc_info=True)
        return []


def _compute_average_price_uplift(
    exceptions: list[InvoiceException],
) -> float | None:
    """Compute the average price uplift percentage across informal modification exceptions."""
    try:
        uplifts = []
        for exc in exceptions:
            try:
                if ExceptionType.INFORMAL_MODIFICATION in exc.exception_types:
                    if exc.purchase_order.total_amount > 0:
                        uplift = (exc.invoice.total_amount - exc.purchase_order.total_amount) / exc.purchase_order.total_amount
                        uplifts.append(uplift)
            except (AttributeError, ZeroDivisionError, TypeError) as e:
                logger.warning(f"Error computing uplift for exception {getattr(exc, 'exception_id', 'unknown')}: {e}")
                continue

        if not uplifts:
            return None

        avg = sum(uplifts) / len(uplifts)
        return avg
    except Exception as e:
        logger.error(f"Error computing average price uplift: {e}", exc_info=True)
        return None
