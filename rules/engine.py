"""Rule evaluation engine for approval automation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from models.exception import InvoiceException, ExceptionType
from rules.models import ApprovalRule, RuleAction, RuleType, RuleEvaluationResult

logger = logging.getLogger(__name__)


class RuleEngine:
    """Evaluate approval rules against exceptions."""

    def __init__(self, rules: list[ApprovalRule]):
        """Initialize with list of rules."""
        self.rules = sorted([r for r in rules if r.enabled], key=lambda x: x.priority)

    def evaluate(self, exception: InvoiceException) -> RuleEvaluationResult | None:
        """Evaluate all rules against exception, return first match."""
        for rule in self.rules:
            result = self._evaluate_rule(rule, exception)
            if result and result.matched:
                return result
        return None

    def evaluate_all(self, exception: InvoiceException) -> list[RuleEvaluationResult]:
        """Evaluate all rules, return all matches."""
        results = []
        for rule in self.rules:
            result = self._evaluate_rule(rule, exception)
            if result:
                results.append(result)
        return results

    def _evaluate_rule(self, rule: ApprovalRule, exc: InvoiceException) -> RuleEvaluationResult:
        """Evaluate a single rule."""
        matched = False
        reason = ""

        try:
            if rule.rule_type == RuleType.AMOUNT_LESS_THAN:
                matched = abs(exc.total_variance_usd) < float(rule.condition_value)
                reason = f"Variance ${abs(exc.total_variance_usd):.2f} < ${rule.condition_value}"

            elif rule.rule_type == RuleType.AMOUNT_GREATER_THAN:
                matched = abs(exc.total_variance_usd) > float(rule.condition_value)
                reason = f"Variance ${abs(exc.total_variance_usd):.2f} > ${rule.condition_value}"

            elif rule.rule_type == RuleType.SUPPLIER_WHITELIST:
                whitelist = str(rule.condition_value).split(",")
                matched = exc.purchase_order.supplier_id in whitelist
                reason = f"Supplier {exc.purchase_order.supplier_id} in whitelist"

            elif rule.rule_type == RuleType.SUPPLIER_BLACKLIST:
                blacklist = str(rule.condition_value).split(",")
                matched = exc.purchase_order.supplier_id in blacklist
                reason = f"Supplier {exc.purchase_order.supplier_id} in blacklist"

            elif rule.rule_type == RuleType.EXCEPTION_TYPE:
                exc_types = str(rule.condition_value).split(",")
                matched = any(exc_type.value in exc_types for exc_type in exc.exception_types)
                reason = f"Exception type in {exc_types}"

            elif rule.rule_type == RuleType.DAYS_OVERDUE:
                now = datetime.now(timezone.utc)
                days_old = (now - exc.created_at).days
                matched = days_old > int(rule.condition_value)
                reason = f"Exception {days_old}d old > {rule.condition_value}d threshold"

            elif rule.rule_type == RuleType.DUPLICATE_SUBMISSION:
                # This would require DB lookup - simplified here
                matched = False
                reason = "Duplicate submission check (requires DB lookup)"

        except Exception as e:
            logger.warning(f"Error evaluating rule {rule.rule_id}: {e}")
            matched = False
            reason = f"Error: {str(e)}"

        return RuleEvaluationResult(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            matched=matched,
            action=rule.action if matched else None,
            reason=reason,
        )

    def get_recommended_action(self, exception: InvoiceException) -> RuleAction | None:
        """Get recommended action from first matching rule."""
        result = self.evaluate(exception)
        return result.action if result else None
