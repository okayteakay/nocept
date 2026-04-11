from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, model_validator


class LineItem(BaseModel):
    """A single line on a supplier invoice."""

    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    unit_of_measure: str = "EA"


class Invoice(BaseModel):
    """A supplier invoice received for payment processing."""

    invoice_id: str
    supplier_id: str
    supplier_name: str
    po_number: str
    invoice_date: date
    currency: str = "USD"
    line_items: list[LineItem]
    tax_amount: Decimal = Decimal("0")
    freight_amount: Decimal = Decimal("0")
    total_amount: Decimal
    raw_payload: dict | None = None

    def computed_total(self) -> Decimal:
        """Return the sum of all line totals plus tax and freight.

        Useful for cross-checking the stated total_amount.
        """
        return sum((item.line_total for item in self.line_items), Decimal("0")) + self.tax_amount + self.freight_amount

    def line_item_by_sku(self, sku: str) -> LineItem | None:
        """Return the first line item matching the given SKU, or None."""
        return next((item for item in self.line_items if item.sku == sku), None)

    @model_validator(mode="after")
    def _validate_line_totals(self) -> "Invoice":
        for item in self.line_items:
            expected = item.quantity * item.unit_price
            if abs(expected - item.line_total) > Decimal("0.02"):
                raise ValueError(
                    f"Line total mismatch for SKU {item.sku}: "
                    f"qty × price = {expected}, line_total = {item.line_total}"
                )
        return self
