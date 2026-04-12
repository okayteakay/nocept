"""
agent/history_checker.py

Step 3 — Historical Approval Check.

Loads dataset/data/historical_approved_exceptions.json and determines whether
the current exception is close enough to a past approved case to warrant
automatic approval.

Simple matching rules:
- same exception_type
- same supplier_id
- approved_date must be before the current invoice date
- variance direction must match
- variance percentage difference must be within 5 percentage points
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from models.exception import InvoiceException

logger = logging.getLogger(__name__)

HISTORICAL_DATA_PATH = (
    Path(__file__).parent.parent / "dataset" / "data" / "historical_approved_exceptions.json"
)

MAX_VARIANCE_DIFF_PCT = 5.0


@dataclass
class HistoricalMatch:
    """A single historical record that matched the current exception."""

    exception_id: str
    supplier_id: str
    exception_type: str
    variance_percentage: float
    approved_date: str
    approved_by: str
    approval_reason: str
    variance_diff: float


class HistoricalCheckResult:
    """Full output of the historical similarity check."""

    def __init__(
        self,
        auto_approve: bool,
        best_match: Optional[HistoricalMatch],
        candidates_checked: int,
        reasoning: str,
    ) -> None:
        self.auto_approve = auto_approve
        self.best_match = best_match
        self.candidates_checked = candidates_checked
        self.reasoning = reasoning


def check_historical_approval(
    exception: InvoiceException,
    data_path: Path = HISTORICAL_DATA_PATH,
) -> HistoricalCheckResult:
    """
    Search the historical approved exceptions dataset for a similar past case.

    Parameters
    ----------
    exception:
        The current InvoiceException being evaluated.
    data_path:
        Path to historical_approved_exceptions.json (override for testing).

    Returns
    -------
    HistoricalCheckResult
    """
    if not data_path.exists():
        logger.warning("Historical approved exceptions file not found: %s", data_path)
        return HistoricalCheckResult(
            auto_approve=False,
            best_match=None,
            candidates_checked=0,
            reasoning="Historical approved exceptions dataset is not available.",
        )

    try:
        with open(data_path) as f:
            records: list[dict] = json.load(f)
    except Exception as exc:
        logger.error("Failed to load historical exceptions: %s", exc)
        return HistoricalCheckResult(
            auto_approve=False,
            best_match=None,
            candidates_checked=0,
            reasoning=f"Could not read historical data: {exc}",
        )

    if not records:
        return HistoricalCheckResult(
            auto_approve=False,
            best_match=None,
            candidates_checked=0,
            reasoning="Historical approved exceptions dataset is empty.",
        )

    # Current invoice date for the date gate
    current_invoice_date = _parse_date(
        getattr(exception.invoice, "invoice_date", None)
    )

    best_match: Optional[HistoricalMatch] = None
    best_variance_diff: float | None = None
    candidates_checked = 0

    for rec in records:
        approved_date = _parse_date(rec.get("approved_date"))
        if approved_date is None:
            continue  # Can't gate on date — skip

        # Date gate: approval must pre-date the current invoice
        if current_invoice_date is not None and approved_date >= current_invoice_date:
            continue

        current_types = {t.value for t in exception.exception_types}
        if rec.get("exception_type", "") not in current_types:
            continue
        if rec.get("supplier_id", "") != exception.invoice.supplier_id:
            continue
        if not _same_variance_direction(
            _current_signed_variance_amount(exception),
            float(rec.get("variance_amount", 0.0)),
        ):
            continue

        variance_diff = abs(
            _current_variance_pct(exception)
            - abs(rec.get("variance_percentage", 0.0))
        )
        candidates_checked += 1

        if best_variance_diff is None or variance_diff < best_variance_diff:
            best_variance_diff = variance_diff
            best_match = HistoricalMatch(
                exception_id=rec.get("exception_id", ""),
                supplier_id=rec.get("supplier_id", ""),
                exception_type=rec.get("exception_type", ""),
                variance_percentage=rec.get("variance_percentage", 0.0),
                approved_date=rec.get("approved_date", ""),
                approved_by=rec.get("approved_by", ""),
                approval_reason=rec.get("approval_reason", ""),
                variance_diff=variance_diff,
            )

    should_approve = bool(
        best_match
        and best_match.variance_diff <= MAX_VARIANCE_DIFF_PCT
    )

    if best_match is None:
        reasoning = (
            f"Checked {candidates_checked} historical records (date-gated). "
            "No date-valid candidates found."
        )
    elif should_approve:
        reasoning = (
            f"Historical match found: {best_match.exception_id} "
            f"Type: {best_match.exception_type}, "
            f"Supplier: {best_match.supplier_id}, "
            f"Variance: {best_match.variance_percentage:+.1f}%, "
            f"Variance gap vs current case: {best_match.variance_diff:.1f} pp. "
            f"Approved: {best_match.approved_date} by {best_match.approved_by}. "
            f"Approval reason on file: \"{best_match.approval_reason}\". "
            f"Auto-approving based on historical precedent."
        )
    else:
        reasoning = (
            f"Checked {candidates_checked} date-valid historical records for this supplier. "
            f"Closest variance gap: {best_match.variance_diff:.1f} pp "
            f"(threshold: {MAX_VARIANCE_DIFF_PCT:.1f} pp). "
            "No sufficiently similar past case found."
        )

    return HistoricalCheckResult(
        auto_approve=should_approve,
        best_match=best_match,
        candidates_checked=candidates_checked,
        reasoning=reasoning,
    )


def _current_variance_pct(exception: InvoiceException) -> float:
    """Return the absolute variance percentage for the current exception."""
    po_total = float(exception.purchase_order.total_amount)
    if po_total <= 0:
        return 0.0
    return abs(exception.total_variance_usd / po_total * 100)


def _current_signed_variance_amount(exception: InvoiceException) -> float:
    """Return the signed invoice minus PO variance amount."""
    return float(exception.invoice.total_amount) - float(exception.purchase_order.total_amount)


def _same_variance_direction(
    current_variance_amount: float,
    historical_variance_amount: float,
) -> bool:
    """Return True when both variances move in the same direction or are both zero."""
    if current_variance_amount == 0 and historical_variance_amount == 0:
        return True
    if current_variance_amount == 0 or historical_variance_amount == 0:
        return False
    return (current_variance_amount > 0) == (historical_variance_amount > 0)


def _parse_date(value: object) -> Optional[date]:
    """Parse a date string or date object to a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
