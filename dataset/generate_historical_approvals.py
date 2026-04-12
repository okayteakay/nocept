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

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # nocept/
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
    ("SUP-010", "CoreFabric Textiles"),
    ("SUP-011", "PrecisionTools Inc"),
    ("SUP-012", "SafeGuard PPE"),
]

# Targeted records that guarantee coverage of specific supplier+type+direction
# combinations that the random generator may miss.  These are always written
# regardless of the random seed.  Dates are in 2024-2025 (clearly past).
TARGETED_RECORDS = [
    # SUP-008 negative price_variance: covers EXC-0003 (-4.95%), diff = 0.45 pp ✓
    {
        "exception_id": "HIST-TGT-0001",
        "po_number": "HIST-TGT-PO-0001",
        "invoice_number": "HIST-TGT-INV-0001",
        "supplier_id": "SUP-008",
        "exception_type": "price_variance",
        "variance_amount": -312.50,
        "variance_percentage": -4.5,
        "description": "Credit adjustment: MedSupply Corp invoiced below PO rate by -4.50%. "
                       "PO $12.50/unit vs invoice $11.94/unit on 500 units.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2024-09-18",
        "approved_by": "Jennifer Walsh, AP Manager",
        "approval_reason": "Supplier issued retroactive volume discount. Variance within acceptable range.",
        "invoice_date": "2024-09-10",
        "po_date": "2024-08-20",
    },
    # SUP-001 positive quantity_variance: covers EXC-0006 (+10%), diff = 0.5 pp ✓
    {
        "exception_id": "HIST-TGT-0002",
        "po_number": "HIST-TGT-PO-0002",
        "invoice_number": "HIST-TGT-INV-0002",
        "supplier_id": "SUP-001",
        "exception_type": "quantity_variance",
        "variance_amount": 487.50,
        "variance_percentage": 9.5,
        "description": "Over-delivery: Apex Paper Co shipped 109 units against PO qty 100 "
                       "(+9 units, +9.50%). Goods received and accepted.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2024-11-22",
        "approved_by": "David Kim, AP Supervisor",
        "approval_reason": "Warehouse confirmed extra units received and accepted per standing agreement with supplier.",
        "invoice_date": "2024-11-14",
        "po_date": "2024-10-30",
    },
    # SUP-005 positive price_variance: covers EXC-0014/EXC-0194 (+8.12%), diff = 0.32 pp ✓
    {
        "exception_id": "HIST-TGT-0003",
        "po_number": "HIST-TGT-PO-0003",
        "invoice_number": "HIST-TGT-INV-0003",
        "supplier_id": "SUP-005",
        "exception_type": "price_variance",
        "variance_amount": 628.80,
        "variance_percentage": 7.8,
        "description": "Unit price uplift: Pacific Packaging invoiced Corrugated Box Single-Wall "
                       "at $3.46/unit vs PO rate $3.21/unit (+7.80%).",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2024-08-05",
        "approved_by": "Carlos Mendez, Finance Controller",
        "approval_reason": "Supplier provided advance notice of raw material cost increase. Approved per procurement policy.",
        "invoice_date": "2024-07-28",
        "po_date": "2024-07-05",
    },
    # SUP-004 negative quantity_variance: covers EXC-0004 (-13.33%), diff = 0.33 pp ✓
    {
        "exception_id": "HIST-TGT-0004",
        "po_number": "HIST-TGT-PO-0004",
        "invoice_number": "HIST-TGT-INV-0004",
        "supplier_id": "SUP-004",
        "exception_type": "quantity_variance",
        "variance_amount": -2040.00,
        "variance_percentage": -13.0,
        "description": "Short delivery: TechParts Direct shipped 130 of 150 ordered PCB Standard FR4 "
                       "(-20 units, -13.00%). Remainder on backorder.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-01-14",
        "approved_by": "Sarah Thompson, Procurement Director",
        "approval_reason": "Supplier confirmed partial shipment due to stock shortage; remainder on backorder. Accepted partial delivery.",
        "invoice_date": "2025-01-06",
        "po_date": "2024-12-10",
    },
    # SUP-010 missing_goods_receipt: covers EXC-0037, EXC-0062
    {
        "exception_id": "HIST-TGT-0005",
        "po_number": "HIST-TGT-PO-0005",
        "invoice_number": "HIST-TGT-INV-0005",
        "supplier_id": "SUP-010",
        "exception_type": "missing_goods_receipt",
        "variance_amount": 0.0,
        "variance_percentage": 0.0,
        "description": "GRN not submitted at time of invoice processing for CoreFabric Textiles PO. "
                       "Goods confirmed delivered via supplier email.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2024-12-03",
        "approved_by": "Patricia Liu, VP Finance",
        "approval_reason": "Physical delivery confirmed via warehouse email. GRN filed retrospectively. Invoice approved.",
        "invoice_date": "2024-11-24",
        "po_date": "2024-11-01",
    },
    # SUP-002 positive quantity_variance: covers EXC-0131 (+5%), diff = 0.5 pp ✓
    {
        "exception_id": "HIST-TGT-0006",
        "po_number": "HIST-TGT-PO-0006",
        "invoice_number": "HIST-TGT-INV-0006",
        "supplier_id": "SUP-002",
        "exception_type": "quantity_variance",
        "variance_amount": 318.75,
        "variance_percentage": 5.5,
        "description": "Over-delivery: SteelCore Industries shipped 211 units against PO qty 200 "
                       "(+11 units, +5.50%). Extra units accepted by warehouse.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-03-20",
        "approved_by": "David Kim, AP Supervisor",
        "approval_reason": "Warehouse confirmed extra units received and accepted per standing agreement with supplier.",
        "invoice_date": "2025-03-12",
        "po_date": "2025-02-20",
    },
    # SUP-011 price_variance: covers EXC-0065 (+15%), diff = 1.5 pp ✓
    {
        "exception_id": "HIST-TGT-0007",
        "po_number": "HIST-TGT-PO-0007",
        "invoice_number": "HIST-TGT-INV-0007",
        "supplier_id": "SUP-011",
        "exception_type": "price_variance",
        "variance_amount": 945.00,
        "variance_percentage": 13.5,
        "description": "Unit price uplift: PrecisionTools Inc invoiced Drill Bit Set Industrial "
                       "at $22.85/unit vs PO rate $20.13/unit (+13.50%).",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-02-11",
        "approved_by": "Jennifer Walsh, AP Manager",
        "approval_reason": "Market price increase confirmed via supplier communication. Variance within acceptable range.",
        "invoice_date": "2025-02-03",
        "po_date": "2025-01-08",
    },
    # SUP-011 missing_goods_receipt: covers EXC-0070
    {
        "exception_id": "HIST-TGT-0008",
        "po_number": "HIST-TGT-PO-0008",
        "invoice_number": "HIST-TGT-INV-0008",
        "supplier_id": "SUP-011",
        "exception_type": "missing_goods_receipt",
        "variance_amount": 0.0,
        "variance_percentage": 0.0,
        "description": "GRN not submitted for PrecisionTools Inc delivery. "
                       "Goods confirmed received by warehouse team.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-04-14",
        "approved_by": "Carlos Mendez, Finance Controller",
        "approval_reason": "Physical delivery confirmed via warehouse email. GRN filed retrospectively. Invoice approved.",
        "invoice_date": "2025-04-07",
        "po_date": "2025-03-15",
    },
    # SUP-011 informal_modification: covers EXC-0074 (+12%), diff = 0.5 pp ✓
    {
        "exception_id": "HIST-TGT-0009",
        "po_number": "HIST-TGT-PO-0009",
        "invoice_number": "HIST-TGT-INV-0009",
        "supplier_id": "SUP-011",
        "exception_type": "informal_modification",
        "variance_amount": 1125.00,
        "variance_percentage": 11.5,
        "description": "Grade upgrade: PO specified Standard Wrench Set, invoice reflects Premium "
                       "grade at $38.50/unit.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-05-30",
        "approved_by": "Sarah Thompson, Procurement Director",
        "approval_reason": "Supplier upgraded grade at buyer's verbal request. Approved by department head.",
        "invoice_date": "2025-05-22",
        "po_date": "2025-04-30",
    },
    # SUP-012 quantity_variance: covers EXC-0048 (-4%), diff = 0.5 pp ✓
    {
        "exception_id": "HIST-TGT-0010",
        "po_number": "HIST-TGT-PO-0010",
        "invoice_number": "HIST-TGT-INV-0010",
        "supplier_id": "SUP-012",
        "exception_type": "quantity_variance",
        "variance_amount": -180.00,
        "variance_percentage": -4.5,
        "description": "Short delivery: SafeGuard PPE shipped 191 of 200 ordered Hard Hats "
                       "(-9 units, -4.50%). Remainder on backorder.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-06-10",
        "approved_by": "David Kim, AP Supervisor",
        "approval_reason": "Supplier confirmed partial shipment; remainder on backorder. Accepted partial delivery.",
        "invoice_date": "2025-06-02",
        "po_date": "2025-05-12",
    },
    # SUP-012 missing_goods_receipt: covers EXC-0154, EXC-0196
    {
        "exception_id": "HIST-TGT-0011",
        "po_number": "HIST-TGT-PO-0011",
        "invoice_number": "HIST-TGT-INV-0011",
        "supplier_id": "SUP-012",
        "exception_type": "missing_goods_receipt",
        "variance_amount": 0.0,
        "variance_percentage": 0.0,
        "description": "GRN not submitted at time of invoice processing for SafeGuard PPE order. "
                       "Delivery confirmed by receiving team.",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-07-22",
        "approved_by": "Patricia Liu, VP Finance",
        "approval_reason": "Physical delivery confirmed via warehouse email. GRN filed retrospectively. Invoice approved.",
        "invoice_date": "2025-07-15",
        "po_date": "2025-06-25",
    },
    # SUP-012 price_variance: general coverage for SUP-012
    {
        "exception_id": "HIST-TGT-0012",
        "po_number": "HIST-TGT-PO-0012",
        "invoice_number": "HIST-TGT-INV-0012",
        "supplier_id": "SUP-012",
        "exception_type": "price_variance",
        "variance_amount": 875.00,
        "variance_percentage": 5.5,
        "description": "Unit price uplift: SafeGuard PPE invoiced N95 Respirators at $21.00/unit "
                       "vs PO rate $19.90/unit (+5.50%).",
        "related_email_ids": [],
        "related_transcript_ids": [],
        "approved_date": "2025-08-19",
        "approved_by": "Jennifer Walsh, AP Manager",
        "approval_reason": "Supplier provided advance notice of raw material cost increase. Approved per procurement policy.",
        "invoice_date": "2025-08-11",
        "po_date": "2025-07-20",
    },
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

    # Merge targeted records — skip any whose exception_id already exists in the
    # randomly generated set (idempotent if run multiple times).
    existing_ids = {r["exception_id"] for r in records}
    added = 0
    for rec in TARGETED_RECORDS:
        if rec["exception_id"] not in existing_ids:
            records.append(rec)
            existing_ids.add(rec["exception_id"])
            added += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nWrote {len(records)} records to {OUTPUT_PATH} "
          f"({len(records) - added} generated + {added} targeted)")


if __name__ == "__main__":
    main()
