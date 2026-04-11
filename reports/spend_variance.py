"""Spend Variance Report.

Aggregates all exceptions resolved with root_cause=UNDOCUMENTED_MODIFICATION
into a structured report that quantifies the dollar impact of informal order
modifications — giving procurement and finance their first structured view of
off-contract spend.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from models.exception import ExceptionState
from models.resolution import Resolution, RootCause
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class SpendVarianceLineItem(BaseModel):
    """One row in the spend variance report, grouped by supplier and period."""

    supplier_id: str
    supplier_name: str
    period: str
    """Quarter string, e.g. "2024-Q1"."""
    category: str
    """Product category derived from line item descriptions."""
    documented_spend: Decimal
    """What the PO said we would spend."""
    actual_spend: Decimal
    """What the invoice said we actually paid."""
    variance: Decimal
    """actual_spend − documented_spend."""
    variance_pct: float
    """variance / documented_spend, as a fraction."""
    undocumented_modification_count: int
    """Number of exceptions contributing to this line."""
    resolution_ids: list[str]
    """Exception IDs that feed into this aggregated line."""


class SpendVarianceReport(BaseModel):
    """Full spend variance report for a reporting period."""

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    period_start: date
    period_end: date
    total_documented_spend: Decimal
    total_actual_spend: Decimal
    total_variance: Decimal
    line_items: list[SpendVarianceLineItem]
    top_suppliers_by_variance: list[SpendVarianceLineItem]
    """Top 10 suppliers by absolute variance, descending."""


def generate_spend_variance_report(
    store: RedisStateStore,
    period_start: date,
    period_end: date,
) -> SpendVarianceReport:
    """Generate the spend variance report for the given date range.

    Queries Redis for all RESOLVED exceptions with
    root_cause=UNDOCUMENTED_MODIFICATION and resolved_at within the period.
    Groups by supplier_id and calendar quarter, then computes documented vs
    actual spend from the PO and invoice totals in each resolution's exception.

    Args:
        store: The Redis state store.
        period_start: Inclusive start date for the report period.
        period_end: Inclusive end date for the report period.

    Returns:
        SpendVarianceReport with all line items and summary totals.
    """
    raise NotImplementedError


def _group_by_supplier_period(
    resolutions: list[Resolution],
) -> dict[tuple[str, str], list[Resolution]]:
    """Group resolutions by (supplier_id, period) key.

    Args:
        resolutions: Resolved exceptions to group.

    Returns:
        Dict mapping (supplier_id, period) → list of Resolution.
    """
    raise NotImplementedError


def _compute_variance_line(
    supplier_id: str,
    period: str,
    resolutions: list[Resolution],
) -> SpendVarianceLineItem:
    """Compute the variance line item for a (supplier, period) group.

    Sums PO totals as documented_spend and invoice totals as actual_spend
    across all resolutions in the group.

    Args:
        supplier_id: Supplier identifier.
        period: Quarter string (e.g. "2024-Q1").
        resolutions: All resolutions in this group.

    Returns:
        SpendVarianceLineItem with aggregated figures.
    """
    raise NotImplementedError


def _to_quarter(d: date) -> str:
    """Convert a date to a quarter string, e.g. "2024-Q1".

    Args:
        d: A date object.

    Returns:
        Quarter string.
    """
    quarter = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{quarter}"


def export_to_csv(report: SpendVarianceReport, output_path: str) -> None:
    """Write the spend variance report line items to a CSV file.

    Args:
        report: The SpendVarianceReport to export.
        output_path: Destination file path.
    """
    raise NotImplementedError
