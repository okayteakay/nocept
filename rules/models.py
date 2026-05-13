"""Data models for approval rules configuration."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class RuleAction(str, Enum):
    """Action to take when rule matches."""
    AUTO_APPROVE = "auto_approve"
    AUTO_REJECT = "auto_reject"
    ESCALATE = "escalate"
    NOTIFY = "notify"


class RuleType(str, Enum):
    """Type of rule condition."""
    AMOUNT_LESS_THAN = "amount_less_than"
    AMOUNT_GREATER_THAN = "amount_greater_than"
    SUPPLIER_WHITELIST = "supplier_whitelist"
    SUPPLIER_BLACKLIST = "supplier_blacklist"
    EXCEPTION_TYPE = "exception_type"
    DAYS_OVERDUE = "days_overdue"
    SUPPLIER_APPROVAL_RATE = "supplier_approval_rate"
    DUPLICATE_SUBMISSION = "duplicate_submission"


class ApprovalRule(BaseModel):
    """Configuration for an approval rule."""
    rule_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(description="Human-readable rule name")
    rule_type: RuleType
    condition_value: str | float | int = Field(description="Value to compare against")
    action: RuleAction
    priority: int = Field(default=100, ge=1, le=1000)
    enabled: bool = Field(default=True)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        return f"[{self.priority}] {self.name}: {self.rule_type.value} {self.action.value}"


class RuleEvaluationResult(BaseModel):
    """Result of evaluating a rule."""
    rule_id: str
    rule_name: str
    matched: bool
    action: RuleAction | None
    reason: str
