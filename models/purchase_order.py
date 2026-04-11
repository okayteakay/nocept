from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class POLineItem(BaseModel):
    """A single line on a Purchase Order."""

    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    unit_of_measure: str = "EA"


class PurchaseOrder(BaseModel):
    """An internal Purchase Order issued to a supplier."""

    po_number: str
    supplier_id: str
    supplier_name: str
    buyer_id: str
    created_date: date
    currency: str = "USD"
    line_items: list[POLineItem]
    tax_amount: Decimal = Decimal("0")
    freight_amount: Decimal = Decimal("0")
    total_amount: Decimal

    def line_item_by_sku(self, sku: str) -> POLineItem | None:
        """Return the first line item matching the given SKU, or None."""
        return next((item for item in self.line_items if item.sku == sku), None)

    def computed_total(self) -> Decimal:
        """Return sum of line totals plus tax and freight."""
        return sum((item.line_total for item in self.line_items), Decimal("0")) + self.tax_amount + self.freight_amount

    @property
    def skus(self) -> set[str]:
        """Return the set of SKUs on this PO."""
        return {item.sku for item in self.line_items}
