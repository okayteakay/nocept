from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from models.exception import ExceptionState


class RootCause(str, Enum):
    """Categorizes the underlying reason for an invoice exception."""

    SYSTEM_ERROR = "system_error"
    BILLING_ERROR = "billing_error"
    UNDOCUMENTED_MODIFICATION = "undocumented_modification"
    POLICY_COMPLIANT_VARIANCE = "policy_compliant_variance"
    DUPLICATE_SUBMISSION = "duplicate_submission"
    UNRESOLVED = "unresolved"


class ResolutionAction(str, Enum):
    """The recommended or taken action for resolving an exception."""

    AUTO_APPROVE = "auto_approve"
    AUTO_REJECT = "auto_reject"
    REQUEST_CREDIT_NOTE = "request_credit_note"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    REQUEST_SUPPLIER_CLARIFICATION = "request_supplier_clarification"


class EvidenceItem(BaseModel):
    """A single piece of supporting evidence for a resolution decision."""

    source: str  # "redis_history" | "tavily_search" | "rule_engine"
    description: str
    url: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ResolutionMemo(BaseModel):
    """Structured output document produced by the agent for each resolved exception."""

    exception_id: str
    root_cause: RootCause
    action: ResolutionAction
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_po_adjustment: Decimal | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Resolution(BaseModel):
    """Final resolution record persisted to Redis after pipeline completion."""

    exception_id: str
    memo: ResolutionMemo
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_by: str = "agent"  # "agent" or an analyst user ID
    final_state: ExceptionState
