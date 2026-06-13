"""ERP Simulator — generates synthetic invoice/PO/GR triples for demos and testing.

This module is the on-demand demo path. For production seeding, the API
loads real data from ``dataset/data/`` via :mod:`ingestion.json_ingestor`.

All generators return ``(Invoice, PurchaseOrder, GoodsReceiptNote | None)``
triples that can be passed to ``agent.classifier.classify_exception`` and
then handed to the LangGraph agent (see ``run_demo.py`` for the canonical
wiring). Used by the dashboard's Demo Trigger to let a user pick a scenario
and run it through the pipeline interactively.
"""
from __future__ import annotations

import random
import uuid
from datetime import date, timedelta

from models.grn import GoodsReceiptNote
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder

# ---------------------------------------------------------------------------
# Reference data matching the Meridian Corp catalog
# ---------------------------------------------------------------------------

_BUYERS = [
    {"name": "Sarah Chen",   "department": "Procurement",  "cost_center": "CC-1001"},
    {"name": "David Park",   "department": "Facilities",   "cost_center": "CC-1002"},
    {"name": "Rachel Gomez", "department": "Operations",   "cost_center": "CC-1003"},
    {"name": "Kevin Walsh",  "department": "Manufacturing", "cost_center": "CC-1004"},
    {"name": "James Liu",    "department": "Warehouse",    "cost_center": "CC-1006"},
]

_RECEIVERS = ["Mike Thompson", "Linda Reyes", "Carlos Diaz", "Anita Patel", "Greg Foster"]

# A subset of catalog products: (supplier_id, supplier_name, sku, description, grade, unit_price)
_PRODUCTS = [
    ("SUP-001", "Apex Paper Co",        "AP-CPA-STD", "A4 Copy Paper Standard",          "Standard",  42.0),
    ("SUP-001", "Apex Paper Co",        "AP-CPA-PRM", "A4 Copy Paper Premium",            "Premium",   58.0),
    ("SUP-001", "Apex Paper Co",        "AP-CSK-STD", "Cardstock Standard 80lb",          "Standard",  65.0),
    ("SUP-001", "Apex Paper Co",        "AP-CSK-PRM", "Cardstock Premium 100lb",          "Premium",   89.0),
    ("SUP-002", "SteelCore Industries", "SC-STL-STD", "Steel Sheet Standard Grade",       "Standard", 180.0),
    ("SUP-002", "SteelCore Industries", "SC-STL-PRM", "Steel Sheet Premium Grade",        "Premium",  240.0),
    ("SUP-008", "MedSupply Corp",       "MS-GLV-STD", "Nitrile Exam Gloves Standard",     "Standard",   9.5),
    ("SUP-008", "MedSupply Corp",       "MS-GLV-SRG", "Nitrile Surgical Gloves Box/50",   "Surgical",  16.0),
    ("SUP-012", "SafeGuard PPE",        "SG-HLM-STD", "Safety Helmet Standard",           "Standard",  25.0),
    ("SUP-012", "SafeGuard PPE",        "SG-HLM-PRO", "Safety Helmet Pro",                "Pro",       38.0),
]

# Known substitute pairs: (po_sku, invoice_sku) — same category, different grade
_SUBSTITUTE_PAIRS = [
    ("AP-CPA-STD", "AP-CPA-PRM"),
    ("AP-CSK-STD", "AP-CSK-PRM"),
    ("SC-STL-STD", "SC-STL-PRM"),
    ("MS-GLV-STD", "MS-GLV-SRG"),
    ("SG-HLM-STD", "SG-HLM-PRO"),
]

_PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "2/10 Net 30"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _po_number() -> str:
    return f"PO-SIM-{uuid.uuid4().hex[:6].upper()}"

def _invoice_number() -> str:
    return f"INV-SIM-{uuid.uuid4().hex[:6].upper()}"

def _gr_number() -> str:
    return f"GR-SIM-{uuid.uuid4().hex[:6].upper()}"

def _today() -> date:
    return date.today()

def _random_buyer() -> dict:
    return random.choice(_BUYERS)

def _random_product() -> tuple:
    """Return (supplier_id, supplier_name, sku, description, grade, unit_price)."""
    return random.choice(_PRODUCTS)

def _random_substitute_pair() -> tuple[tuple, tuple]:
    """Return two product tuples (po_product, invoice_product) that are known substitutes."""
    po_sku, inv_sku = random.choice(_SUBSTITUTE_PAIRS)
    po_prod  = next(p for p in _PRODUCTS if p[2] == po_sku)
    inv_prod = next(p for p in _PRODUCTS if p[2] == inv_sku)
    return po_prod, inv_prod


# ---------------------------------------------------------------------------
# Scenario generators
# ---------------------------------------------------------------------------

def generate_straight_through_invoice(
    supplier_id: str | None = None,
    po_number: str | None = None,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate a clean three-way match with no exceptions.

    PO, invoice, and GR all agree on SKU, quantity, price, and total.

    Args:
        supplier_id: Fix the supplier; picks randomly if None.
        po_number: PO number to use; auto-generated if None.

    Returns:
        (Invoice, PurchaseOrder, GoodsReceiptNote) with matching totals.
    """
    print(f"  [Scenario: Straight-Through] Generating clean match...")
    sup_id, sup_name, sku, desc, grade, price = _random_product()
    if supplier_id is not None:
        prods = [p for p in _PRODUCTS if p[0] == supplier_id]
        if prods:
            sup_id, sup_name, sku, desc, grade, price = random.choice(prods)

    qty = random.randint(50, 300)
    total = round(price * qty, 2)
    po_num = po_number or _po_number()
    inv_num = _invoice_number()
    buyer = _random_buyer()
    po_date = _today() - timedelta(days=random.randint(20, 40))
    inv_date = po_date + timedelta(days=random.randint(7, 20))
    terms = random.choice(_PAYMENT_TERMS)
    due_date = inv_date + timedelta(days=int(terms.split()[-1]))

    line = LineItem(sku=sku, description=desc, product_grade=grade,
                    unit_price=price, quantity=qty, total=total)

    po = PurchaseOrder(
        po_number=po_num, supplier_id=sup_id, supplier_name=sup_name,
        line_items=[line], total_amount=total,
        creation_date=po_date, created_by=buyer["name"],
        department=buyer["department"], cost_center=buyer["cost_center"],
    )
    invoice = Invoice(
        invoice_number=inv_num, po_number=po_num, supplier_id=sup_id,
        supplier_name=sup_name, line_items=[line], total_amount=total,
        invoice_date=inv_date, due_date=due_date, payment_terms=terms,
    )
    gr = GoodsReceiptNote(
        gr_number=_gr_number(), po_number=po_num, invoice_number=inv_num,
        supplier_id=sup_id,
        line_items=[line],
        date_received=inv_date - timedelta(days=2),
        received_by=random.choice(_RECEIVERS),
    )
    return invoice, po, gr


def generate_price_variance_exception(
    supplier_id: str | None = None,
    variance_pct: float = 0.08,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an invoice with a unit price variance outside the 5% tolerance.

    PO and GR quantities match; only the invoice unit price differs.

    Args:
        supplier_id: Optional fixed supplier.
        variance_pct: Fractional price overcharge (e.g. 0.08 = 8%).
    """
    print(f"  [Scenario: Price Variance] Generating invoice with {variance_pct*100}% unit price overcharge...")
    sup_id, sup_name, sku, desc, grade, po_price = _random_product()
    if supplier_id is not None:
        prods = [p for p in _PRODUCTS if p[0] == supplier_id]
        if prods:
            sup_id, sup_name, sku, desc, grade, po_price = random.choice(prods)

    qty = random.randint(50, 200)
    inv_price = round(po_price * (1 + variance_pct), 2)
    po_total = round(po_price * qty, 2)
    inv_total = round(inv_price * qty, 2)
    po_num = _po_number()
    inv_num = _invoice_number()
    buyer = _random_buyer()
    po_date = _today() - timedelta(days=random.randint(20, 40))
    inv_date = po_date + timedelta(days=random.randint(7, 20))
    terms = random.choice(_PAYMENT_TERMS)
    due_date = inv_date + timedelta(days=int(terms.split()[-1]))

    po_line = LineItem(sku=sku, description=desc, product_grade=grade,
                       unit_price=po_price, quantity=qty, total=po_total)
    inv_line = LineItem(sku=sku, description=desc, product_grade=grade,
                        unit_price=inv_price, quantity=qty, total=inv_total)

    po = PurchaseOrder(
        po_number=po_num, supplier_id=sup_id, supplier_name=sup_name,
        line_items=[po_line], total_amount=po_total,
        creation_date=po_date, created_by=buyer["name"],
        department=buyer["department"], cost_center=buyer["cost_center"],
    )
    invoice = Invoice(
        invoice_number=inv_num, po_number=po_num, supplier_id=sup_id,
        supplier_name=sup_name, line_items=[inv_line], total_amount=inv_total,
        invoice_date=inv_date, due_date=due_date, payment_terms=terms,
    )
    gr = GoodsReceiptNote(
        gr_number=_gr_number(), po_number=po_num, invoice_number=inv_num,
        supplier_id=sup_id, line_items=[po_line],
        date_received=inv_date - timedelta(days=2),
        received_by=random.choice(_RECEIVERS),
    )
    return invoice, po, gr


def generate_informal_modification_exception(
    supplier_id: str | None = None,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an informal modification scenario — full product grade substitution.

    PO: qty × Grade A (lower price)
    Invoice: same qty × Grade B (higher price, different SKU)
    GR: matches invoice (not PO)

    This is the primary demo scenario: the supplier substituted a higher-grade
    product due to stock shortage with a verbal agreement, and no PO amendment
    was filed.
    """
    print(f"  [Scenario: Informal Mod] Generating product grade substitution...")
    po_prod, inv_prod = _random_substitute_pair()
    sup_id, sup_name = po_prod[0], po_prod[1]
    po_sku, po_desc, po_grade, po_price = po_prod[2], po_prod[3], po_prod[4], po_prod[5]
    inv_sku, inv_desc, inv_grade, inv_price = inv_prod[2], inv_prod[3], inv_prod[4], inv_prod[5]

    # Canonical partial-substitution scenario:
    # PO: qty units of po_sku @ po_price
    # Invoice: (qty - sub_qty) po_sku + sub_qty inv_sku (higher price)
    # GR: matches invoice quantities
    qty = random.randint(80, 300)
    sub_qty = random.randint(max(10, qty // 5), qty // 3)  # 20-33% substituted
    main_qty = qty - sub_qty

    po_total = round(po_price * qty, 2)
    inv_main_total = round(po_price * main_qty, 2)
    inv_sub_total = round(inv_price * sub_qty, 2)
    inv_total = round(inv_main_total + inv_sub_total, 2)

    po_num = _po_number()
    inv_num = _invoice_number()
    buyer = _random_buyer()
    po_date = _today() - timedelta(days=random.randint(20, 40))
    inv_date = po_date + timedelta(days=random.randint(7, 20))
    terms = random.choice(_PAYMENT_TERMS)
    due_date = inv_date + timedelta(days=int(terms.split()[-1]))

    po_line = LineItem(sku=po_sku, description=po_desc, product_grade=po_grade,
                       unit_price=po_price, quantity=qty, total=po_total)
    inv_main_line = LineItem(sku=po_sku, description=po_desc, product_grade=po_grade,
                             unit_price=po_price, quantity=main_qty, total=inv_main_total)
    inv_sub_line = LineItem(sku=inv_sku, description=inv_desc, product_grade=inv_grade,
                            unit_price=inv_price, quantity=sub_qty, total=inv_sub_total)

    po = PurchaseOrder(
        po_number=po_num, supplier_id=sup_id, supplier_name=sup_name,
        line_items=[po_line], total_amount=po_total,
        creation_date=po_date, created_by=buyer["name"],
        department=buyer["department"], cost_center=buyer["cost_center"],
    )
    invoice = Invoice(
        invoice_number=inv_num, po_number=po_num, supplier_id=sup_id,
        supplier_name=sup_name, line_items=[inv_main_line, inv_sub_line],
        total_amount=inv_total,
        invoice_date=inv_date, due_date=due_date, payment_terms=terms,
    )
    gr = GoodsReceiptNote(
        gr_number=_gr_number(), po_number=po_num, invoice_number=inv_num,
        supplier_id=sup_id, line_items=[inv_main_line, inv_sub_line],
        date_received=inv_date - timedelta(days=2),
        received_by=random.choice(_RECEIVERS),
        notes=(
            f"Received {main_qty} {po_grade} and {sub_qty} {inv_grade}. "
            "Product substitution — no change order on file."
        ),
    )
    return invoice, po, gr


def generate_expedited_shipping_exception(
    supplier_id: str | None = None,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate an expedited shipping surcharge exception.

    PO has one product line. Invoice adds a SHIP-EXP line for expedited shipping
    agreed verbally by the buyer. GR matches the product line only (no GR for freight).
    """
    print(f"  [Scenario: Expedited Shipping] Adding shipping surcharge line item...")
    sup_id, sup_name, sku, desc, grade, price = _random_product()
    if supplier_id is not None:
        prods = [p for p in _PRODUCTS if p[0] == supplier_id]
        if prods:
            sup_id, sup_name, sku, desc, grade, price = random.choice(prods)

    qty = random.randint(100, 500)
    po_total = round(price * qty, 2)
    surcharge = round(po_total * random.uniform(0.05, 0.15), 2)
    inv_total = round(po_total + surcharge, 2)
    po_num = _po_number()
    inv_num = _invoice_number()
    buyer = _random_buyer()
    po_date = _today() - timedelta(days=random.randint(20, 40))
    inv_date = po_date + timedelta(days=random.randint(7, 20))
    terms = random.choice(_PAYMENT_TERMS)
    due_date = inv_date + timedelta(days=int(terms.split()[-1]))

    product_line = LineItem(sku=sku, description=desc, product_grade=grade,
                            unit_price=price, quantity=qty, total=po_total)
    shipping_line = LineItem(
        sku="SHIP-EXP",
        description="Expedited Shipping Surcharge",
        product_grade="N/A",
        unit_price=surcharge,
        quantity=1,
        total=surcharge,
    )

    po = PurchaseOrder(
        po_number=po_num, supplier_id=sup_id, supplier_name=sup_name,
        line_items=[product_line], total_amount=po_total,
        creation_date=po_date, created_by=buyer["name"],
        department=buyer["department"], cost_center=buyer["cost_center"],
    )
    invoice = Invoice(
        invoice_number=inv_num, po_number=po_num, supplier_id=sup_id,
        supplier_name=sup_name, line_items=[product_line, shipping_line],
        total_amount=inv_total,
        invoice_date=inv_date, due_date=due_date, payment_terms=terms,
    )
    gr = GoodsReceiptNote(
        gr_number=_gr_number(), po_number=po_num, invoice_number=inv_num,
        supplier_id=sup_id, line_items=[product_line],
        date_received=inv_date - timedelta(days=1),
        received_by=random.choice(_RECEIVERS),
        notes="Rush delivery — expedited shipping per buyer request.",
    )
    return invoice, po, gr


def generate_quantity_variance_exception(
    supplier_id: str | None = None,
    shortfall_pct: float = 0.15,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote]:
    """Generate a partial shipment where the invoice quantity is less than the PO.

    Args:
        supplier_id: Optional fixed supplier.
        shortfall_pct: Fraction of PO quantity missing from the invoice (e.g. 0.15 = 15%).
    """
    print(f"  [Scenario: Quantity Variance] Generating partial shipment ({shortfall_pct*100}% shortfall)...")
    sup_id, sup_name, sku, desc, grade, price = _random_product()
    po_qty = random.randint(100, 400)
    inv_qty = max(1, int(po_qty * (1 - shortfall_pct)))
    po_total = round(price * po_qty, 2)
    inv_total = round(price * inv_qty, 2)
    po_num = _po_number()
    inv_num = _invoice_number()
    buyer = _random_buyer()
    po_date = _today() - timedelta(days=random.randint(20, 40))
    inv_date = po_date + timedelta(days=random.randint(7, 20))
    terms = random.choice(_PAYMENT_TERMS)
    due_date = inv_date + timedelta(days=int(terms.split()[-1]))

    po_line = LineItem(sku=sku, description=desc, product_grade=grade,
                       unit_price=price, quantity=po_qty, total=po_total)
    inv_line = LineItem(sku=sku, description=desc, product_grade=grade,
                        unit_price=price, quantity=inv_qty, total=inv_total)

    po = PurchaseOrder(
        po_number=po_num, supplier_id=sup_id, supplier_name=sup_name,
        line_items=[po_line], total_amount=po_total,
        creation_date=po_date, created_by=buyer["name"],
        department=buyer["department"], cost_center=buyer["cost_center"],
    )
    invoice = Invoice(
        invoice_number=inv_num, po_number=po_num, supplier_id=sup_id,
        supplier_name=sup_name, line_items=[inv_line], total_amount=inv_total,
        invoice_date=inv_date, due_date=due_date, payment_terms=terms,
    )
    gr = GoodsReceiptNote(
        gr_number=_gr_number(), po_number=po_num, invoice_number=inv_num,
        supplier_id=sup_id, line_items=[inv_line],
        date_received=inv_date - timedelta(days=2),
        received_by=random.choice(_RECEIVERS),
        notes=f"Partial delivery: {inv_qty} of {po_qty} ordered units received.",
    )
    return invoice, po, gr


def generate_missing_receipt_exception(
    supplier_id: str | None = None,
) -> tuple[Invoice, PurchaseOrder, None]:
    """Generate an invoice with no corresponding goods receipt.

    Returns:
        (Invoice, PurchaseOrder, None) — GR is explicitly None.
    """
    invoice, po, _gr = generate_straight_through_invoice(supplier_id)
    return invoice, po, None


def generate_duplicate_exception(
    original_invoice: Invoice,
) -> Invoice:
    """Return a near-duplicate of the original invoice with a new invoice_number.

    Models the common scenario of a supplier re-submitting an already-processed
    invoice. The duplicate shares all line items, amounts, and po_number but
    has a fresh invoice_number and today's invoice_date.

    Args:
        original_invoice: The original Invoice to duplicate.
    """
    terms = original_invoice.payment_terms
    new_inv_date = _today()
    due_offset = int(terms.split()[-1])
    return Invoice(
        invoice_number=f"{original_invoice.invoice_number}-DUP",
        po_number=original_invoice.po_number,
        supplier_id=original_invoice.supplier_id,
        supplier_name=original_invoice.supplier_name,
        line_items=original_invoice.line_items,
        total_amount=original_invoice.total_amount,
        invoice_date=new_inv_date,
        due_date=new_inv_date + timedelta(days=due_offset),
        payment_terms=terms,
        currency=original_invoice.currency,
    )
