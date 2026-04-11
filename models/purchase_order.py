from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from models.invoice import LineItem


class PurchaseOrder(BaseModel):
    """An internal Purchase Order issued to a supplier."""

    po_number: str
    supplier_id: str
    supplier_name: str
    line_items: list[LineItem]
    total_amount: float
    creation_date: date
    created_by: str
    department: str
    cost_center: str
    currency: str = "USD"

    def line_item_by_sku(self, sku: str) -> LineItem | None:
        """Return the first line item matching the given SKU, or None."""
        return next((item for item in self.line_items if item.sku == sku), None)

    def computed_total(self) -> float:
        """Return the sum of all line item totals."""
        return round(sum(item.total for item in self.line_items), 2)

    @property
    def skus(self) -> set[str]:
        """Return the set of SKUs on this PO."""
        return {item.sku for item in self.line_items}
