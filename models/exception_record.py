"""ExceptionRecord — the pre-computed exception entry from the dataset.

ExceptionType is defined here (not in exception.py) to avoid a circular
import: exception.py imports ExceptionRecord, so ExceptionType must live
in a module that exception.py can safely import from.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ExceptionType(str, Enum):
    """All recognized invoice exception types in the Meridian Corp dataset."""

    NONE = "none"
    PRICE_VARIANCE = "price_variance"
    QUANTITY_VARIANCE = "quantity_variance"
    MISSING_GOODS_RECEIPT = "missing_goods_receipt"
    DUPLICATE_INVOICE = "duplicate_invoice"
    INFORMAL_MODIFICATION = "informal_modification"


class ExceptionRecord(BaseModel):
    """A pre-computed exception record from the dataset.

    Generated alongside the invoice/PO/GR triplets; captures the known exception
    type, dollar variance, and links to any related email or phone communications.

    The agent ingests these alongside the source documents and uses the linked
    communications as evidence for resolution.
    """

    exception_id: str
    po_number: str
    invoice_number: str
    supplier_id: str
    exception_type: ExceptionType
    variance_amount: float
    variance_percentage: float
    description: str
    related_email_ids: list[str] = []
    related_transcript_ids: list[str] = []

    @property
    def has_communications(self) -> bool:
        """Return True if any email or transcript is linked to this exception."""
        return bool(self.related_email_ids or self.related_transcript_ids)

    @property
    def is_exception(self) -> bool:
        """Return True if this record represents an actual exception (not a clean invoice)."""
        return self.exception_type != ExceptionType.NONE
