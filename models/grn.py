from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from models.purchase_order import PurchaseOrder


class GRNLineItem(BaseModel):
    """A single line in a Goods Receipt Note."""

    sku: str
    quantity_received: Decimal
    received_date: date
    condition: str = "acceptable"  # "acceptable" | "damaged" | "rejected"


class GoodsReceiptNote(BaseModel):
    """Records what was physically received from a supplier against a PO."""

    grn_id: str
    po_number: str
    supplier_id: str
    receipt_date: date
    line_items: list[GRNLineItem]

    def quantity_received_for_sku(self, sku: str) -> Decimal:
        """Return total quantity received for a given SKU across all GRN lines.

        Returns Decimal("0") if the SKU is not present.
        """
        return sum(
            (item.quantity_received for item in self.line_items if item.sku == sku),
            Decimal("0"),
        )

    def is_complete_receipt(self, po: "PurchaseOrder") -> bool:
        """Return True if all PO SKUs were received in full, within a 2% tolerance."""
        for po_line in po.line_items:
            received = self.quantity_received_for_sku(po_line.sku)
            if po_line.quantity == Decimal("0"):
                continue
            shortfall_pct = abs(received - po_line.quantity) / po_line.quantity
            if shortfall_pct > Decimal("0.02"):
                return False
        return True

    @property
    def skus_received(self) -> set[str]:
        """Return the set of SKUs present in this GRN."""
        return {item.sku for item in self.line_items}
