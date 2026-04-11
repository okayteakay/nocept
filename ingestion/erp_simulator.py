"""ERP Simulator — generates realistic invoice/PO/GRN tuples for demos and testing.

All generators return (Invoice, PurchaseOrder, GoodsReceiptNote | None) triples
that can be passed directly to agent.pipeline.run_pipeline.

Run as a script to print a sample batch summary::

    python -m ingestion.erp_simulator
"""
from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from models.grn import GoodsReceiptNote, GRNLineItem
from models.invoice import Invoice, LineItem
from models.purchase_order import POLineItem, PurchaseOrder

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today() -> date:
    return date.today()


def _po_number() -> str:
    return f"PO-{uuid.uuid4().hex[:8].upper()}"


def _invoice_number() -> str:
    return f"INV-{uuid.uuid4().hex[:8].upper()}"


def _grn_number() -> str:
    return f"GRN-{uuid.uuid4().hex[:8].upper()}"


# ---------------------------------------------------------------------------
# Individual scenario generators
# ---------------------------------------------------------------------------

def generate_straight_through_invoice(
    supplier_id: str = "SUPP-001",
    po_number: str | None = None,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate a clean three-way match with no exceptions.

    PO, invoice, and GRN all agree on quantity, price, and SKU.

    Args:
        supplier_id: Supplier identifier to embed in all three documents.
        po_number: PO number to use; auto-generated if None.

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote) with matching totals.
    """
    raise NotImplementedError


def generate_price_variance_exception(
    supplier_id: str = "SUPP-001",
    variance_pct: float = 0.08,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an invoice with a price variance outside the 5% auto-approval threshold.

    PO: 100 units @ $100 = $10,000
    Invoice: 100 units @ $100 × (1 + variance_pct) = $10,800 (at 8% variance)
    GRN: matches PO quantity.

    Args:
        supplier_id: Supplier identifier.
        variance_pct: Fractional price overcharge (e.g. 0.08 = 8%).

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote).
    """
    raise NotImplementedError


def generate_informal_modification_exception(
    supplier_id: str = "SUPP-001",
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate the canonical informal modification scenario.

    PO:      500 × Grade A Paper @ $50.00 = $25,000
    Invoice: 450 × Grade A Paper @ $50.00 = $22,500
             + 50 × Grade B Paper @ $80.00 = $4,000
             Total invoice = $26,500
    GRN: matches invoice quantities (not PO).

    This is the primary demo scenario — the supplier substituted 50 reams of
    Grade B for Grade A due to a stock shortage, with a verbal agreement.

    Args:
        supplier_id: Supplier identifier.

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote).
    """
    raise NotImplementedError


def generate_quantity_variance_exception(
    supplier_id: str = "SUPP-001",
    shortfall_pct: float = 0.15,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an invoice where the billed quantity is less than ordered.

    Args:
        supplier_id: Supplier identifier.
        shortfall_pct: Fraction of PO quantity missing from the invoice.

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote).
    """
    raise NotImplementedError


def generate_missing_receipt_exception(
    supplier_id: str = "SUPP-001",
) -> tuple[Invoice, PurchaseOrder, None]:
    """Generate an invoice with no corresponding GRN.

    Args:
        supplier_id: Supplier identifier.

    Returns:
        (Invoice, PurchaseOrder, None) — grn is explicitly None.
    """
    raise NotImplementedError


def generate_duplicate_exception(
    original_invoice: Invoice,
) -> Invoice:
    """Return a near-duplicate of the original invoice with a new invoice_id.

    Models the common scenario of a supplier re-submitting an invoice that was
    already processed. The duplicate has a new invoice_id but identical line
    items, amounts, and po_number.

    Args:
        original_invoice: The original Invoice to duplicate.

    Returns:
        A new Invoice with a fresh invoice_id and today's invoice_date.
    """
    raise NotImplementedError


def generate_tax_discrepancy_exception(
    supplier_id: str = "SUPP-001",
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an invoice where the tax amount doesn't match the PO.

    Args:
        supplier_id: Supplier identifier.

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote).
    """
    raise NotImplementedError


def generate_batch(
    n: int,
    exception_rate: float = 0.25,
    supplier_ids: list[str] | None = None,
) -> list[tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None]]:
    """Generate n invoice sets with the given exception rate.

    The mix of exception types within the exception fraction is:
    - 40% informal modification
    - 30% price variance
    - 15% quantity variance
    - 10% missing receipt
    - 5% tax discrepancy

    Args:
        n: Total number of invoice sets to generate.
        exception_rate: Fraction of invoices that should have exceptions (0–1).
        supplier_ids: Optional list of supplier IDs to cycle through.
                      Defaults to ["SUPP-001", "SUPP-002", "SUPP-003"].

    Returns:
        List of (Invoice, PurchaseOrder, GoodsReceiptNote | None) tuples.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Script entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Generating demo batch of 20 invoice sets (25% exception rate)...")
    batch = generate_batch(20, exception_rate=0.25)
    print(f"Generated {len(batch)} sets")
    for i, (inv, po, grn) in enumerate(batch, 1):
        grn_label = grn.grn_id if grn else "MISSING"
        print(f"  {i:2d}. Invoice {inv.invoice_id} | PO {po.po_number} | GRN {grn_label} | Total ${inv.total_amount}")
