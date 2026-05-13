"""Calculate KPIs and business metrics from exceptions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import defaultdict

from models.exception import ExceptionState, InvoiceException
from state.redis_backend import RedisStateStore


class AnalyticsCalculator:
    """Calculate KPIs, trends, and supplier scorecards."""

    def __init__(self, store: RedisStateStore):
        self.store = store

    def get_all_exceptions(self) -> list[InvoiceException]:
        """Load all exceptions from store."""
        all_ids: set[str] = set(self.store.list_queue_ids())
        for state in ExceptionState:
            all_ids.update(self.store.list_by_state(state))

        exceptions: list[InvoiceException] = []
        for exc_id in all_ids:
            try:
                exceptions.append(self.store.load(exc_id))
            except KeyError:
                continue

        return exceptions

    def filter_by_date_range(
        self,
        exceptions: list[InvoiceException],
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[InvoiceException]:
        """Filter exceptions by date range."""
        if not date_from and not date_to:
            return exceptions

        filtered = []
        for exc in exceptions:
            exc_date = exc.created_at
            if date_from and exc_date < date_from:
                continue
            if date_to and exc_date > date_to:
                continue
            filtered.append(exc)

        return filtered

    def calculate_kpis(self, exceptions: list[InvoiceException]) -> dict:
        """Calculate all KPIs."""
        if not exceptions:
            return {
                "total_exceptions": 0,
                "auto_resolved": 0,
                "auto_resolution_rate": 0.0,
                "manual_approved": 0,
                "manual_approval_rate": 0.0,
                "rejected": 0,
                "escalated": 0,
                "sla_compliance_pct": 0.0,
                "avg_resolution_hours": 0.0,
                "cost_at_risk": 0.0,
                "cost_saved": 0.0,
                "cost_of_exceptions": 0.0,
            }

        total = len(exceptions)
        auto_resolved = sum(1 for e in exceptions if e.state == ExceptionState.RESOLVED and not e.approved_by)
        manual_approved = sum(1 for e in exceptions if e.state == ExceptionState.APPROVED)
        rejected = sum(1 for e in exceptions if e.state == ExceptionState.REJECTED)
        escalated = sum(1 for e in exceptions if e.state == ExceptionState.ESCALATED)

        # Cost metrics
        cost_at_risk = sum(
            abs(e.total_variance_usd)
            for e in exceptions
            if e.state in (ExceptionState.ESCALATED, ExceptionState.PENDING_APPROVAL)
        )
        cost_of_exceptions = sum(abs(e.total_variance_usd) for e in exceptions)
        cost_saved = sum(
            abs(e.total_variance_usd) for e in exceptions if e.state == ExceptionState.REJECTED
        )

        # SLA compliance (resolved within 24h)
        resolved_in_time = 0
        resolution_times = []
        for e in exceptions:
            if e.state in (ExceptionState.RESOLVED, ExceptionState.APPROVED, ExceptionState.REJECTED):
                if e.approved_by or e.rejected_by:
                    timestamp = e.approval_timestamp or e.rejection_timestamp
                    if timestamp:
                        time_diff = timestamp - e.created_at
                        resolution_times.append(time_diff.total_seconds() / 3600)
                        if time_diff <= timedelta(hours=24):
                            resolved_in_time += 1
                else:
                    # Auto-resolved
                    resolution_times.append(0)
                    resolved_in_time += 1

        sla_compliance = (resolved_in_time / max(total, 1)) * 100 if total > 0 else 0
        avg_resolution_hours = sum(resolution_times) / max(len(resolution_times), 1) if resolution_times else 0

        return {
            "total_exceptions": total,
            "auto_resolved": auto_resolved,
            "auto_resolution_rate": (auto_resolved / total * 100) if total > 0 else 0,
            "manual_approved": manual_approved,
            "manual_approval_rate": (manual_approved / total * 100) if total > 0 else 0,
            "rejected": rejected,
            "escalated": escalated,
            "sla_compliance_pct": sla_compliance,
            "avg_resolution_hours": avg_resolution_hours,
            "cost_at_risk": round(cost_at_risk, 2),
            "cost_saved": round(cost_saved, 2),
            "cost_of_exceptions": round(cost_of_exceptions, 2),
        }

    def calculate_supplier_scorecard(self, exceptions: list[InvoiceException]) -> list[dict]:
        """Calculate metrics per supplier."""
        by_supplier: dict[str, list[InvoiceException]] = defaultdict(list)

        for exc in exceptions:
            key = (exc.purchase_order.supplier_id, exc.supplier_name)
            by_supplier[key].append(exc)

        scorecard = []
        for (supplier_id, supplier_name), supplier_excs in sorted(by_supplier.items()):
            total = len(supplier_excs)
            approved = sum(1 for e in supplier_excs if e.state == ExceptionState.APPROVED)
            rejected = sum(1 for e in supplier_excs if e.state == ExceptionState.REJECTED)

            approval_rate = (approved / total * 100) if total > 0 else 0
            avg_variance = sum(abs(e.total_variance_usd) for e in supplier_excs) / max(total, 1)

            # Most common exception type
            type_counts: dict = defaultdict(int)
            for e in supplier_excs:
                for exc_type in e.exception_types:
                    type_counts[exc_type.value] += 1

            most_common_type = max(type_counts.items(), key=lambda x: x[1])[0] if type_counts else "none"

            scorecard.append({
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "exception_count": total,
                "approved_count": approved,
                "rejected_count": rejected,
                "approval_rate": round(approval_rate, 1),
                "avg_variance": round(avg_variance, 2),
                "most_common_type": most_common_type,
            })

        # Sort by exception count descending
        scorecard.sort(key=lambda x: x["exception_count"], reverse=True)
        return scorecard

    def calculate_trends(self, exceptions: list[InvoiceException]) -> dict:
        """Calculate trend data for charts."""
        # Daily trend (last 30 days)
        now = datetime.now(timezone.utc)
        daily_counts: dict[str, int] = defaultdict(int)

        for e in exceptions:
            date_key = e.created_at.strftime("%Y-%m-%d")
            daily_counts[date_key] += 1

        daily_trend = [
            {"date": k, "count": v} for k, v in sorted(daily_counts.items())
        ]

        # By exception type
        type_counts: dict[str, int] = defaultdict(int)
        for e in exceptions:
            for exc_type in e.exception_types:
                type_counts[exc_type.value] += 1

        type_trend = [
            {"type": k, "count": v} for k, v in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        # By status
        status_counts: dict[str, int] = defaultdict(int)
        for e in exceptions:
            status_counts[e.state.value] += 1

        status_trend = [
            {"status": k, "count": v} for k, v in sorted(status_counts.items())
        ]

        return {
            "daily": daily_trend,
            "by_type": type_trend,
            "by_status": status_trend,
        }

    def get_summary(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict:
        """Get complete analytics summary."""
        all_exceptions = self.get_all_exceptions()
        filtered = self.filter_by_date_range(all_exceptions, date_from, date_to)

        return {
            "kpis": self.calculate_kpis(filtered),
            "supplier_scorecard": self.calculate_supplier_scorecard(filtered),
            "trends": self.calculate_trends(filtered),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
