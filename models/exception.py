from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder


class ExceptionType(str, Enum):
    """Enumeration of all recognized invoice exception types."""

    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    MISSING_RECEIPT = "missing_receipt"
    DUPLICATE = "duplicate"
    TAX_DISCREPANCY = "tax_discrepancy"
    FREIGHT_DISCREPANCY = "freight_discrepancy"
    CURRENCY_CONVERSION = "currency_conversion"
    INFORMAL_MODIFICATION = "informal_modification"
    UNKNOWN = "unknown"


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
    po_quantity: Decimal | None = None
    invoice_quantity: Decimal | None = None
    po_unit_price: Decimal | None = None
    invoice_unit_price: Decimal | None = None
    quantity_delta: Decimal | None = None     # invoice_qty - po_qty
    price_delta_pct: float | None = None      # (invoice_price - po_price) / po_price
    is_new_sku: bool = False                  # SKU present on invoice but not on PO


class InvoiceException(BaseModel):
    """A three-way match failure requiring investigation and resolution."""

    exception_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invoice: Invoice
    purchase_order: PurchaseOrder
    grn: GoodsReceiptNote | None
    exception_types: list[ExceptionType] = Field(default_factory=list)
    state: ExceptionState = ExceptionState.RECEIVED
    line_variances: list[LineItemVariance] = Field(default_factory=list)
    total_variance_usd: Decimal = Decimal("0")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict = Field(default_factory=dict)

    def has_exception_type(self, exc_type: ExceptionType) -> bool:
        """Return True if this exception includes the given type."""
        return exc_type in self.exception_types

    def mark_updated(self) -> None:
        """Update the updated_at timestamp to now."""
        self.updated_at = datetime.utcnow()
