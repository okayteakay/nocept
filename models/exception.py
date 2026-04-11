from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from models.communication import Email, PhoneTranscript
from models.exception_record import ExceptionRecord, ExceptionType
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder


class ExceptionState(str, Enum):
    """State machine states for an InvoiceException lifecycle."""

    RECEIVED = "received"
    TRIAGED = "triaged"
    RESEARCHING = "researching"
    PENDING_APPROVAL = "pending_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class LineItemVariance(BaseModel):
    """Captures the delta between a single invoice line and its PO counterpart."""

    sku: str
    description: str
    po_quantity: int | None = None
    invoice_quantity: int | None = None
    po_unit_price: float | None = None
    invoice_unit_price: float | None = None
    quantity_delta: int | None = None          # invoice_qty - po_qty
    price_delta_pct: float | None = None       # (invoice_price - po_price) / po_price
    is_new_sku: bool = False                   # SKU on invoice but not on PO
    is_expedited_shipping: bool = False        # SHIP-EXP or similar surcharge SKU


class InvoiceException(BaseModel):
    """The agent's working object for a three-way match failure.

    Wraps the invoice/PO/GR source documents together with the pre-computed
    ExceptionRecord from the dataset, any linked communications, and all
    agent-computed fields (state, variances, classification).
    """

    exception_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invoice: Invoice
    purchase_order: PurchaseOrder
    grn: GoodsReceiptNote | None

    # Pre-computed record from the dataset (None if agent classifies on the fly)
    exception_record: ExceptionRecord | None = None

    # Communications linked to this exception — the primary evidence for informal modifications
    related_emails: list[Email] = Field(default_factory=list)
    related_transcripts: list[PhoneTranscript] = Field(default_factory=list)

    # Agent-computed fields
    exception_types: list[ExceptionType] = Field(default_factory=list)
    state: ExceptionState = ExceptionState.RECEIVED
    line_variances: list[LineItemVariance] = Field(default_factory=list)
    total_variance_usd: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def has_exception_type(self, exc_type: ExceptionType) -> bool:
        """Return True if this exception includes the given type."""
        return exc_type in self.exception_types

    def mark_updated(self) -> None:
        """Update the updated_at timestamp to now."""
        self.updated_at = datetime.now(timezone.utc)

    @property
    def has_communications(self) -> bool:
        """Return True if any emails or transcripts are linked."""
        return bool(self.related_emails or self.related_transcripts)

    @property
    def supplier_name(self) -> str:
        """Convenience accessor for the supplier name from the invoice."""
        return self.invoice.supplier_name
