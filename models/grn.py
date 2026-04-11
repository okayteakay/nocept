from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel

from models.invoice import LineItem

if TYPE_CHECKING:
    from models.purchase_order import PurchaseOrder


class GoodsReceiptNote(BaseModel):
    """Records what was physically received from a supplier against a PO and invoice."""

    gr_number: str
    po_number: str
    invoice_number: str
    supplier_id: str
    line_items: list[LineItem]
    date_received: date
    received_by: str
    notes: str | None = None

    def quantity_received_for_sku(self, sku: str) -> int:
        """Return total quantity received for a given SKU. Returns 0 if SKU absent."""
        return sum(item.quantity for item in self.line_items if item.sku == sku)

    def is_complete_receipt(self, po: "PurchaseOrder") -> bool:
        """Return True if all PO SKUs were received in full within a 2% tolerance.

        Note: does not check for extra SKUs on the GR that are absent from the PO.
        Those are captured as INFORMAL_MODIFICATION signals by the classifier.
        """
        for po_line in po.line_items:
            if po_line.quantity == 0:
                continue
            received = self.quantity_received_for_sku(po_line.sku)
            shortfall_pct = abs(received - po_line.quantity) / po_line.quantity
            if shortfall_pct > 0.02:
                return False
        return True

    @property
    def skus_received(self) -> set[str]:
        """Return the set of SKUs present on this goods receipt."""
        return {item.sku for item in self.line_items}
