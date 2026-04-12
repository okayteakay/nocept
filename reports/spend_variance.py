"""Spend Variance Report.

Aggregates all exceptions resolved with root_cause=UNDOCUMENTED_MODIFICATION
into a structured report that quantifies the dollar impact of informal order
modifications — giving procurement and finance their first structured view of
off-contract spend.
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from models.exception import ExceptionState, InvoiceException
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

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
    if period_start > period_end:
        raise ValueError("period_start must be on or before period_end")

    # Pull all resolved exceptions and pair each with its resolution record.
    resolution_pairs: list[tuple[Resolution, InvoiceException]] = []
    for exception_id in store.list_by_state(ExceptionState.RESOLVED):
        resolution = store.get_resolution(exception_id)
        if resolution is None:
            continue

        if resolution.memo.root_cause != RootCause.UNDOCUMENTED_MODIFICATION:
            continue

        resolved_date = resolution.resolved_at.date()
        if resolved_date < period_start or resolved_date > period_end:
            continue

        try:
            exc = store.load(exception_id)
        except KeyError:
            logger.warning(
                "Resolution references missing exception %s — skipping",
                exception_id,
            )
            continue

        resolution_pairs.append((resolution, exc))

    grouped = _group_by_supplier_period(resolution_pairs)
    line_items: list[SpendVarianceLineItem] = []
    for (supplier_id, period, category), pairs in grouped.items():
        line_items.append(_compute_variance_line(supplier_id, period, category, pairs))

    line_items.sort(key=lambda li: (li.period, li.supplier_name, li.category))

    total_documented_spend = sum(
        (li.documented_spend for li in line_items), Decimal("0")
    )
    total_actual_spend = sum(
        (li.actual_spend for li in line_items), Decimal("0")
    )
    total_variance = sum((li.variance for li in line_items), Decimal("0"))

    supplier_rollup: dict[str, SpendVarianceLineItem] = {}
    for li in line_items:
        if li.supplier_id not in supplier_rollup:
            supplier_rollup[li.supplier_id] = SpendVarianceLineItem(
                supplier_id=li.supplier_id,
                supplier_name=li.supplier_name,
                period="All",
                category="All",
                documented_spend=Decimal("0"),
                actual_spend=Decimal("0"),
                variance=Decimal("0"),
                variance_pct=0.0,
                undocumented_modification_count=0,
                resolution_ids=[],
            )

        agg = supplier_rollup[li.supplier_id]
        agg.documented_spend += li.documented_spend
        agg.actual_spend += li.actual_spend
        agg.variance += li.variance
        agg.undocumented_modification_count += li.undocumented_modification_count
        agg.resolution_ids.extend(li.resolution_ids)

    for agg in supplier_rollup.values():
        if agg.documented_spend > 0:
            agg.variance_pct = float(agg.variance / agg.documented_spend)
        else:
            agg.variance_pct = 0.0

    top_suppliers_by_variance = sorted(
        supplier_rollup.values(),
        key=lambda li: abs(li.variance),
        reverse=True,
    )[:10]

    return SpendVarianceReport(
        period_start=period_start,
        period_end=period_end,
        total_documented_spend=total_documented_spend,
        total_actual_spend=total_actual_spend,
        total_variance=total_variance,
        line_items=line_items,
        top_suppliers_by_variance=top_suppliers_by_variance,
    )


def _group_by_supplier_period(
    resolution_pairs: list[tuple[Resolution, InvoiceException]],
) -> dict[tuple[str, str, str], list[tuple[Resolution, InvoiceException]]]:
    """Group resolution pairs by (supplier_id, period, category) key.

    Args:
        resolution_pairs: Tuples of (Resolution, InvoiceException).

    Returns:
        Dict mapping (supplier_id, period, category) → list of pairs.
    """
    grouped: dict[tuple[str, str, str], list[tuple[Resolution, InvoiceException]]] = (
        defaultdict(list)
    )
    for resolution, exc in resolution_pairs:
        period = _to_quarter(resolution.resolved_at.date())
        category = _infer_category(exc)
        key = (exc.invoice.supplier_id, period, category)
        grouped[key].append((resolution, exc))
    return dict(grouped)


def _compute_variance_line(
    supplier_id: str,
    period: str,
    category: str,
    resolution_pairs: list[tuple[Resolution, InvoiceException]],
) -> SpendVarianceLineItem:
    """Compute the variance line item for a (supplier, period) group.

    Sums PO totals as documented_spend and invoice totals as actual_spend
    across all resolutions in the group.

    Args:
        supplier_id: Supplier identifier.
        period: Quarter string (e.g. "2024-Q1").
        category: Product category label for this group.
        resolution_pairs: All (Resolution, InvoiceException) entries in this group.

    Returns:
        SpendVarianceLineItem with aggregated figures.
    """
    supplier_name = (
        resolution_pairs[0][1].invoice.supplier_name
        if resolution_pairs
        else supplier_id
    )

    documented_spend = Decimal("0")
    actual_spend = Decimal("0")
    resolution_ids: list[str] = []
    for resolution, exc in resolution_pairs:
        documented_spend += Decimal(str(exc.purchase_order.total_amount))
        actual_spend += Decimal(str(exc.invoice.total_amount))
        resolution_ids.append(resolution.exception_id)

    variance = actual_spend - documented_spend
    variance_pct = float(variance / documented_spend) if documented_spend > 0 else 0.0

    return SpendVarianceLineItem(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        period=period,
        category=category,
        documented_spend=documented_spend,
        actual_spend=actual_spend,
        variance=variance,
        variance_pct=variance_pct,
        undocumented_modification_count=len(resolution_pairs),
        resolution_ids=resolution_ids,
    )


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
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "supplier_id",
                "supplier_name",
                "period",
                "category",
                "documented_spend",
                "actual_spend",
                "variance",
                "variance_pct",
                "undocumented_modification_count",
                "resolution_ids",
            ]
        )
        for line in report.line_items:
            writer.writerow(
                [
                    line.supplier_id,
                    line.supplier_name,
                    line.period,
                    line.category,
                    str(line.documented_spend),
                    str(line.actual_spend),
                    str(line.variance),
                    line.variance_pct,
                    line.undocumented_modification_count,
                    ",".join(line.resolution_ids),
                ]
            )


def _infer_category(exc: InvoiceException) -> str:
    """Infer a broad product category from invoice line items."""
    descriptions = " ".join(li.description.lower() for li in exc.invoice.line_items)
    skus = [li.sku for li in exc.invoice.line_items]

    keyword_map = {
        "paper": "Paper",
        "cardstock": "Paper",
        "steel": "Metals",
        "glove": "Medical Supplies",
        "medical": "Medical Supplies",
        "helmet": "Safety Equipment",
        "ppe": "Safety Equipment",
    }
    for keyword, category in keyword_map.items():
        if keyword in descriptions:
            return category

    if skus:
        prefix = skus[0].split("-")[0]
        prefix_map = {
            "AP": "Paper",
            "SC": "Metals",
            "MS": "Medical Supplies",
            "SG": "Safety Equipment",
        }
        return prefix_map.get(prefix, "Other")

    return "Unknown"
