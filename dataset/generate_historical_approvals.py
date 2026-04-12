"""
dataset/generators/generate_historical_approvals.py

Generates dataset/data/historical_approved_exceptions.json — a dataset of
past approved exceptions used by Step 3 (historical similarity check).

Run from the project root:
    python dataset/generators/generate_historical_approvals.py

Output: dataset/data/historical_approved_exceptions.json
  - Same base columns as exception_records.json
  - Extra fields: approved_date, approved_by, approval_reason, invoice_date, po_date

Design notes
------------
- Dates are in 2024-01 through 2025-12 so they are clearly in the PAST
  relative to the current invoice data (which is in early 2026).
- Distribution mirrors the current exception_records.json types.
- Some records intentionally match current exceptions (same supplier + type)
  so that Step 3 can fire an auto-approval.
- DUPLICATE_INVOICE type is excluded — those are always AUTO_REJECT.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "dataset" / "data" / "historical_approved_exceptions.json"

SUPPLIERS = [
    ("SUP-001", "Apex Paper Co"),
    ("SUP-002", "SteelCore Industries"),
    ("SUP-003", "BrightChem Solutions"),
    ("SUP-004", "TechParts Direct"),
    ("SUP-005", "Pacific Packaging"),
    ("SUP-006", "GreenLeaf Office Supplies"),
    ("SUP-007", "ProBuild Materials"),
    ("SUP-008", "MedSupply Corp"),
    ("SUP-009", "FreshFlow Beverages"),
    ("SUP-010", "SafeGuard PPE"),
]

APPROVERS = [
    "Jennifer Walsh, AP Manager",
    "Carlos Mendez, Finance Controller",
    "Patricia Liu, VP Finance",
    "David Kim, AP Supervisor",
    "Sarah Thompson, Procurement Director",
]

# (exception_type, variance_pct_range, description_template, approval_reason_template)
EXCEPTION_TEMPLATES = [
    (
        "price_variance",
        (1.5, 8.0),
        "Unit price mismatch: PO has ${po_price:.2f}, invoice has ${inv_price:.2f} ({var_pct:+.2f}%)",
        "Market price increase confirmed via supplier communication. Variance within acceptable range.",
    ),
    (
        "price_variance",
        (0.8, 4.5),
        "Price uplift on {sku}: PO rate ${po_price:.2f}, invoiced at ${inv_price:.2f} ({var_pct:+.2f}%)",
        "Supplier provided advance notice of raw material cost increase. Approved per procurement policy.",
    ),
    (
        "price_variance",
        (-3.0, -1.0),
        "Credit adjustment: invoiced below PO rate by {var_pct:.2f}%. PO ${po_price:.2f} vs invoice ${inv_price:.2f}",
        "Supplier issued volume discount retrospectively. Approved.",
    ),
    (
        "quantity_variance",
        (-15.0, -5.0),
        "Short delivery: PO qty {po_qty}, received {inv_qty} (diff {diff:+d} units).",
        "Supplier confirmed partial shipment due to stock shortage; remainder on backorder. Accepted partial delivery.",
    ),
    (
        "quantity_variance",
        (5.0, 12.0),
        "Over-delivery: PO qty {po_qty}, invoice qty {inv_qty} (diff {diff:+d} units). Goods received.",
        "Warehouse confirmed extra units received and accepted per standing agreement with supplier.",
    ),
    (
        "informal_modification",
        (10.0, 45.0),
        "Product substitution: PO requested {orig_desc}; invoice shows {sub_desc} at {inv_price:.2f}/unit.",
        "Substituted product confirmed equivalent by engineering. Original SKU discontinued by manufacturer.",
    ),
    (
        "informal_modification",
        (2.0, 20.0),
        "Grade upgrade: PO specified Standard grade, invoice reflects Premium grade at {inv_price:.2f}/unit.",
        "Supplier upgraded grade at buyer's verbal request. Email confirmation obtained from procurement team.",
    ),
    (
        "informal_modification",
        (8.0, 35.0),
        "SKU swap: {orig_sku} replaced with {sub_sku}. Price changed from ${po_price:.2f} to ${inv_price:.2f}/unit.",
        "Product line change announced by supplier. Nearest available substitute approved by department head.",
    ),
    (
        "missing_goods_receipt",
        (0.0, 0.0),
        "GRN not submitted at time of invoice processing for PO {po_number}. Goods confirmed delivered.",
        "Physical delivery confirmed via warehouse email. GRN filed retrospectively. Invoice approved.",
    ),
]

SKUS = [
    ("PAPER-A4-STD", "A4 Copy Paper Standard 80gsm"),
    ("PAPER-A4-PRE", "A4 Copy Paper Premium 90gsm"),
    ("STEEL-BAR-HR", "Hot-Rolled Steel Bar 25mm"),
    ("CHEM-SOL-IPA", "Isopropyl Alcohol 99.9% 20L"),
    ("TECH-CAP-EL", "Electrolytic Capacitor 100uF"),
    ("PACK-BOX-SW", "Corrugated Box Single-Wall 12x12x12"),
    ("OFFC-INK-BK", "Black Toner Cartridge HP Compatible"),
    ("BLDG-CMT-GEN", "Portland Cement General Purpose 25kg"),
    ("MED-GLV-STD", "Nitrile Exam Gloves Standard Box/100"),
    ("BEV-COF-STD", "Coffee Beans Standard Blend 5lb"),
    ("PPE-MASK-N95", "N95 Respirator Mask Box/20"),
    ("STEEL-SHT-CR", "Cold-Rolled Steel Sheet 1mm 1000x2000"),
    ("CHEM-CHL-SYN", "Chlorinated Solvent Synthetic Grade 5L"),
    ("TECH-PCBD-4L", "PCB Prototype 4-Layer 100x100mm"),
    ("PACK-FIL-BUB", "Bubble Wrap Roll 500mm x 100m"),
]


def _random_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _make_record(idx: int, rng: random.Random) -> dict:
    supplier_id, supplier_name = rng.choice(SUPPLIERS)
    template = rng.choice(EXCEPTION_TEMPLATES)
    exc_type, var_range, desc_tmpl, approval_reason = template

    po_date = _random_date(date(2024, 1, 1), date(2025, 9, 30))
    invoice_date = po_date + timedelta(days=rng.randint(7, 45))
    approved_date = invoice_date + timedelta(days=rng.randint(3, 21))

    po_price = round(rng.uniform(5.0, 80.0), 2)
    var_pct = round(rng.uniform(*var_range), 2)
    inv_price = round(po_price * (1 + var_pct / 100), 2)
    po_qty = rng.randint(20, 500)
    qty_delta = int(po_qty * var_pct / 100)
    inv_qty = po_qty + qty_delta
    variance_amount = round((inv_price - po_price) * po_qty, 2)

    sku, desc = rng.choice(SKUS)
    alt_sku, alt_desc = rng.choice([s for s in SKUS if s[0] != sku])

    po_number = f"HIST-PO-{idx:04d}"
    description = desc_tmpl.format(
        po_price=po_price,
        inv_price=inv_price,
        var_pct=var_pct,
        po_qty=po_qty,
        inv_qty=inv_qty,
        diff=inv_qty - po_qty,
        orig_desc=desc,
        sub_desc=alt_desc,
        orig_sku=sku,
        sub_sku=alt_sku,
        sku=sku,
        po_number=po_number,
    )

    email_ids: list[str] = []
    transcript_ids: list[str] = []
    if exc_type == "informal_modification" and rng.random() > 0.4:
        email_ids = [f"HIST-EMAIL-{idx:04d}"]
    if exc_type == "missing_goods_receipt" and rng.random() > 0.5:
        email_ids = [f"HIST-EMAIL-{idx:04d}"]

    return {
        "exception_id": f"HIST-EXC-{idx:04d}",
        "po_number": po_number,
        "invoice_number": f"HIST-INV-{idx:04d}",
        "supplier_id": supplier_id,
        "exception_type": exc_type,
        "variance_amount": variance_amount,
        "variance_percentage": var_pct,
        "description": description,
        "related_email_ids": email_ids,
        "related_transcript_ids": transcript_ids,
        # Extra fields for historical approved exceptions
        "approved_date": approved_date.isoformat(),
        "approved_by": rng.choice(APPROVERS),
        "approval_reason": approval_reason,
        "invoice_date": invoice_date.isoformat(),
        "po_date": po_date.isoformat(),
    }


def generate(n: int = 55, seed: int = 42) -> list[dict]:
    """Generate n historical approved exception records."""
    rng = random.Random(seed)
    records = [_make_record(i + 1, rng) for i in range(n)]

    # Ensure good type distribution
    types_needed = {
        "price_variance": 18,
        "quantity_variance": 14,
        "informal_modification": 18,
        "missing_goods_receipt": 5,
    }
    type_counts = {t: 0 for t in types_needed}
    for rec in records:
        t = rec["exception_type"]
        if t in type_counts:
            type_counts[t] += 1

    # Print summary
    print("Generated historical approved exceptions:")
    for t, count in type_counts.items():
        print(f"  {t}: {count}")
    print(f"Total: {len(records)}")
    return records


def main() -> None:
    records = generate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nWrote {len(records)} records to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
