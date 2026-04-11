from __future__ import annotations

from datetime import date

from pydantic import BaseModel, model_validator

PAYMENT_TERMS = {"Net 30", "Net 45", "Net 60", "2/10 Net 30"}


class LineItem(BaseModel):
    """A single line item shared by Invoice, PurchaseOrder, and GoodsReceiptNote.

    All three document types use this identical structure in the Meridian Corp dataset.
    """

    sku: str
    description: str
    product_grade: str
    unit_price: float
    quantity: int
    total: float


class Invoice(BaseModel):
    """A supplier invoice received for payment processing."""

    invoice_number: str
    po_number: str
    supplier_id: str
    supplier_name: str
    line_items: list[LineItem]
    total_amount: float
    invoice_date: date
    due_date: date
    payment_terms: str  # "Net 30" | "Net 45" | "Net 60" | "2/10 Net 30"
    currency: str = "USD"

    def computed_total(self) -> float:
        """Return the sum of all line item totals."""
        return round(sum(item.total for item in self.line_items), 2)

    def line_item_by_sku(self, sku: str) -> LineItem | None:
        """Return the first line item matching the given SKU, or None."""
        return next((item for item in self.line_items if item.sku == sku), None)

    @property
    def skus(self) -> set[str]:
        """Return the set of SKUs billed on this invoice."""
        return {item.sku for item in self.line_items}

    @model_validator(mode="after")
    def _validate_line_totals(self) -> "Invoice":
        for item in self.line_items:
            expected = round(item.unit_price * item.quantity, 2)
            if abs(expected - item.total) > 0.02:
                raise ValueError(
                    f"Line total mismatch for SKU {item.sku}: "
                    f"qty × price = {expected}, total = {item.total}"
                )
        return self
