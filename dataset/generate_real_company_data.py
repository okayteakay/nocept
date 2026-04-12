"""
dataset/generators/generate_real_company_data.py

For each candidate company we run a targeted Tavily search.
If Tavily returns a result with score >= MIN_SCORE the company is included;
otherwise it is skipped entirely.  This guarantees:
  - Clean, real company names (we supply the name, Tavily validates it)
  - Every description is backed by a real snippet Step 5 can rediscover
  - No junk rows from article titles

Usage
-----
  python dataset/generators/generate_real_company_data.py            # add new rows
  python dataset/generators/generate_real_company_data.py --rebuild  # strip & regenerate
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Load .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV = PROJECT_ROOT / ".env"
if _ENV.exists():
    for _l in _ENV.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _, _v = _l.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

DATA_DIR    = PROJECT_ROOT / "dataset" / "data"
REAL_PREFIX = "REAL-"
MIN_SCORE   = 0.60   # minimum Tavily relevance score to accept a result

# ---------------------------------------------------------------------------
# Candidate companies
# (name, category, exception_type, product_line, query)
# query is targeted to that specific company + exception event
# ---------------------------------------------------------------------------
CANDIDATES = [
    # --- Price variance ---
    ("UPS",                     "Logistics",        "price_variance",        "Ground Freight Standard Service",        "UPS general rate increase 2024 announcement"),
    ("FedEx",                   "Logistics",        "price_variance",        "Express Shipping Standard Service",      "FedEx rate increase 2024 surcharge announcement"),
    ("Nucor Corporation",       "Raw Materials",    "price_variance",        "Hot-Rolled Steel Coil",                  "Nucor steel price increase announcement 2024"),
    ("United States Steel",     "Raw Materials",    "price_variance",        "Cold-Rolled Steel Sheet",                "United States Steel price increase flat rolled 2024"),
    ("Eastman Chemical",        "Chemicals",        "price_variance",        "Isopropyl Alcohol 99% Industrial",       "Eastman Chemical price increase solvents 2024"),
    ("Dow Chemical",            "Chemicals",        "price_variance",        "Polyurethane Resin Industrial",          "Dow Chemical price increase announcement 2024"),
    ("International Paper",     "Office Supplies",  "price_variance",        "A4 Copy Paper Premium Ream",             "International Paper price increase uncoated freesheet 2024"),
    ("Georgia-Pacific",         "Office Supplies",  "price_variance",        "Copy Paper 20lb Bond",                   "Georgia-Pacific paper price increase 2024"),
    ("WestRock",                "Packaging",        "price_variance",        "Corrugated Box Single-Wall",             "WestRock corrugated containerboard price increase 2024"),
    ("Sealed Air",              "Packaging",        "price_variance",        "Bubble Wrap Protective Packaging",       "Sealed Air price increase packaging 2024"),
    ("Medline Industries",      "Healthcare",       "price_variance",        "Nitrile Examination Gloves Box/100",     "Medline Industries price increase gloves 2024"),
    ("Grainger",                "Facilities",       "price_variance",        "Industrial Maintenance Supply Kit",      "Grainger price increase MRO supplies 2024"),
    ("Fastenal",                "Facilities",       "price_variance",        "Stainless Steel Fasteners M8",           "Fastenal price increase fasteners 2024"),
    ("Sysco",                   "Food Service",     "price_variance",        "Coffee Arabica Beans 5lb",               "Sysco food price increase commodity ingredients 2024"),
    ("US Foods",                "Food Service",     "price_variance",        "Canola Oil Bulk 35lb",                   "US Foods price increase food ingredients 2024"),
    ("Holcim",                  "Construction",     "price_variance",        "Portland Cement Type I/II 94lb",         "Holcim cement price increase announcement 2024"),
    ("USG Corporation",         "Construction",     "price_variance",        "Drywall Panel 5/8 inch Type X",          "USG drywall price increase 2024"),
    # --- Informal modification (substitution / discontinuation) ---
    ("3M",                      "Healthcare",       "informal_modification", "N95 Respirator Mask Box/20",             "3M product discontinued substitute alternative 2024"),
    ("BASF",                    "Chemicals",        "informal_modification", "Polyurethane Dispersion Coating",        "BASF product reformulated discontinued substitute 2024"),
    ("Owens Corning",           "Construction",     "informal_modification", "Fiberglass Insulation Batts R-19",       "Owens Corning insulation product discontinued substitute 2024"),
    ("Sonoco",                  "Packaging",        "informal_modification", "Industrial Paper Tubes",                 "Sonoco product discontinued alternative packaging 2024"),
    ("Cardinal Health",         "Healthcare",       "informal_modification", "Surgical Drapes Sterile",                "Cardinal Health product discontinued substitute healthcare 2024"),
    ("Arrow Electronics",       "Electronics",      "informal_modification", "FPGA Development Board",                 "Arrow Electronics end of life component substitute 2024"),
    ("Mouser Electronics",      "Electronics",      "informal_modification", "Microcontroller Unit STM32",             "Mouser Electronics end of life product substitution 2024"),
    ("Domtar",                  "Office Supplies",  "informal_modification", "Business Paper Standard Ream",           "Domtar paper product discontinued substitute 2024"),
    # --- Quantity variance (shortage / partial shipment) ---
    ("TTI Inc",                 "Electronics",      "quantity_variance",     "Electrolytic Capacitors 100uF",          "TTI electronic components shortage partial shipment 2024"),
    ("Owens & Minor",           "Healthcare",       "quantity_variance",     "Medical Exam Gloves Box/100",            "Owens Minor medical supply shortage backorder 2024"),
    ("McKesson",                "Healthcare",       "quantity_variance",     "Sterile Saline Solution 1L",             "McKesson medical supply shortage partial fulfillment 2024"),
    ("Worthington Industries",  "Raw Materials",    "quantity_variance",     "Steel Pressure Cylinders",               "Worthington Industries supply shortage partial shipment 2024"),
    ("Performance Food Group",  "Food Service",     "quantity_variance",     "Single-Use Food Containers",             "Performance Food Group supply shortage partial delivery 2024"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(p: Path) -> list[dict]:
    return json.loads(p.read_text()) if p.exists() else []

def save_json(p: Path, data: list[dict]) -> None:
    p.write_text(json.dumps(data, indent=2))

def strip_real(data: list[dict], key: str) -> list[dict]:
    return [r for r in data if REAL_PREFIX not in r.get(key, "")]

def _next_sup_id(suppliers: list[dict]) -> str:
    nums = [int(m.group(1)) for s in suppliers if (m := re.match(r"SUP-(\d+)", s.get("supplier_id", "")))]
    return f"SUP-{max(nums, default=0) + 1:03d}"

def _rand_date(start: date, end: date, rng: random.Random) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))

def search(query: str, client) -> list[dict]:
    try:
        return client.search(query, max_results=3).get("results", [])
    except Exception as e:
        print(f"    Tavily error: {e}")
        return []

def best_result(results: list[dict], company_name: str) -> tuple[float, str, str, str]:
    """Return (score, title, url, excerpt) for the most relevant result."""
    first_word = company_name.split()[0].lower()
    # Prefer results that mention the company name
    for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
        content = r.get("content", "")
        title   = r.get("title", "")
        if first_word in (title + content).lower():
            return (
                r.get("score", 0),
                title,
                r.get("url", ""),
                content[:350].replace("\n", " ").strip(),
            )
    # Fallback to highest-scored result regardless
    if results:
        r = max(results, key=lambda x: x.get("score", 0))
        return (
            r.get("score", 0),
            r.get("title", ""),
            r.get("url", ""),
            r.get("content", "")[:350].replace("\n", " ").strip(),
        )
    return 0.0, "", "", ""


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def build_row(
    idx: int,
    company_name: str,
    category: str,
    exception_type: str,
    product_line: str,
    supplier_id: str,
    rng: random.Random,
    snippet: str,
    source_url: str,
) -> tuple[dict, dict, dict, dict, dict | None]:
    tag = f"{REAL_PREFIX}{idx:03d}"

    po_date  = _rand_date(date(2025, 9, 1), date(2026, 3, 1), rng)
    inv_date = po_date + timedelta(days=rng.randint(7, 35))

    po_price = round(rng.uniform(10.0, 100.0), 2)
    qty      = rng.randint(25, 400)

    if exception_type == "price_variance":
        var_pct   = round(rng.uniform(2.5, 12.0), 2)
        inv_price = round(po_price * (1 + var_pct / 100), 2)
        inv_qty   = qty
    elif exception_type == "quantity_variance":
        var_pct   = round(-rng.uniform(5.0, 20.0), 2)
        inv_price = po_price
        inv_qty   = max(1, int(qty * (1 + var_pct / 100)))
    else:  # informal_modification
        var_pct   = round(rng.uniform(5.0, 28.0), 2)
        inv_price = round(po_price * (1 + var_pct / 100), 2)
        inv_qty   = qty

    po_total        = round(po_price * qty, 2)
    inv_total       = round(inv_price * inv_qty, 2)
    variance_amount = round(inv_total - po_total, 2)

    sku     = f"{REAL_PREFIX}{re.sub(r'[^A-Z0-9]', '', category.upper()[:6])}-{supplier_id}"
    sub_sku = f"{sku}-SUB"

    buyers = ["Alex Rivera", "Jordan Lee", "Morgan Chen", "Taylor Kim", "Casey Park"]
    depts  = ["Procurement", "Operations", "Facilities", "Finance", "Manufacturing"]
    ccs    = ["CC-2001", "CC-2002", "CC-2003", "CC-2004", "CC-2005"]

    po_line  = {"sku": sku,     "description": product_line,                    "product_grade": "Standard", "unit_price": po_price, "quantity": qty,     "total": po_total}
    inv_line = (
        {"sku": sub_sku, "description": f"{product_line} (Substituted / Next Available)", "product_grade": "Premium",  "unit_price": inv_price, "quantity": inv_qty, "total": inv_total}
        if exception_type == "informal_modification"
        else {"sku": sku, "description": product_line, "product_grade": "Standard", "unit_price": inv_price, "quantity": inv_qty, "total": inv_total}
    )

    supplier = {
        "supplier_id": supplier_id,
        "name":           company_name,
        "contact_person": "Accounts Receivable",
        "contact_email":  f"ar@{re.sub(r'[^a-z0-9]', '', company_name.lower())[:20]}.com",
        "phone":          f"+1-555-{9000 + idx:04d}",
        "category":       category,
    }
    po = {
        "po_number":    f"PO-{tag}",
        "supplier_id":  supplier_id, "supplier_name": company_name,
        "line_items":   [po_line],   "total_amount":  po_total,
        "creation_date": po_date.isoformat(),
        "created_by":   rng.choice(buyers),
        "department":   rng.choice(depts),
        "cost_center":  rng.choice(ccs),
    }
    invoice = {
        "invoice_number": f"INV-{tag}",
        "po_number":      f"PO-{tag}",
        "supplier_id":    supplier_id, "supplier_name": company_name,
        "line_items":     [inv_line],  "total_amount":  inv_total,
        "invoice_date":   inv_date.isoformat(),
        "due_date":       (inv_date + timedelta(days=30)).isoformat(),
        "payment_terms":  rng.choice(["Net 30", "Net 45", "2/10 Net 30"]),
    }
    goods_receipt = {
        "gr_number": f"GR-{tag}",
        "po_number": f"PO-{tag}",
        "invoice_number": f"INV-{tag}",
        "supplier_id": supplier_id,
        "line_items": [inv_line],
        "date_received": (inv_date - timedelta(days=rng.randint(1, 5))).isoformat(),
        "received_by": rng.choice(
            ["Chris Nolan", "Jamie Brooks", "Priya Shah", "Luis Gomez", "Dana Ellis"]
        ),
        "notes": {
            "price_variance": "Goods received as ordered. Invoice amount differs from PO.",
            "quantity_variance": "Partial delivery recorded by receiving team.",
            "informal_modification": "Substituted item received; no formal PO amendment on file.",
        }[exception_type],
    }

    description = (
        f"{company_name} ({category}) — {exception_type.replace('_', ' ')}. "
        f"PO: {product_line} @ ${po_price:.2f} x {qty} = ${po_total:.2f}. "
        f"Invoice: ${inv_price:.2f} x {inv_qty} = ${inv_total:.2f} ({var_pct:+.1f}%). "
        + (f'Web source: "{snippet[:220]}" ({source_url})' if snippet else "")
    )

    exc_rec = {
        "exception_id":         f"EXC-{tag}",
        "po_number":            f"PO-{tag}",
        "invoice_number":       f"INV-{tag}",
        "supplier_id":          supplier_id,
        "exception_type":       exception_type,
        "variance_amount":      variance_amount,
        "variance_percentage":  var_pct,
        "description":          description,
        "related_email_ids":    [],
        "related_transcript_ids": [],
    }

    return supplier, po, invoice, exc_rec, goods_receipt


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true",
                        help="Strip all previous REAL rows and regenerate fresh.")
    args = parser.parse_args()

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        print("ERROR: TAVILY_API_KEY not set in .env")
        sys.exit(1)

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        print(f"Tavily ready (key: {api_key[:12]}...)\n")
    except ImportError:
        print("ERROR: run  uv pip install tavily-python")
        sys.exit(1)

    suppliers_data = load_json(DATA_DIR / "suppliers.json")
    invoices_data  = load_json(DATA_DIR / "invoices.json")
    pos_data       = load_json(DATA_DIR / "purchase_orders.json")
    goods_receipts_data = load_json(DATA_DIR / "goods_receipts.json")
    exc_data       = load_json(DATA_DIR / "exception_records.json")

    if args.rebuild:
        print("--rebuild: stripping existing REAL rows...")
        suppliers_data = strip_real(suppliers_data, "supplier_id")
        invoices_data  = strip_real(invoices_data,  "invoice_number")
        pos_data       = strip_real(pos_data,        "po_number")
        goods_receipts_data = strip_real(goods_receipts_data, "gr_number")
        exc_data       = strip_real(exc_data,         "exception_id")
        print(f"  Kept {len(suppliers_data)} suppliers, {len(exc_data)} exceptions.\n")

    existing_names = {s.get("name", "").lower() for s in suppliers_data}

    rng = random.Random(42)
    new_suppliers, new_invoices, new_pos, new_goods_receipts, new_exceptions = [], [], [], [], []
    skipped = []

    print(f"{'Company':<28} {'Type':<24} {'Score':>5}  Result")
    print("-" * 75)

    row_idx = 1
    for company_name, category, exc_type, product_line, query in CANDIDATES:
        if company_name.lower() in existing_names:
            print(f"  {'SKIP':<6} {company_name} — already in dataset")
            continue

        results = search(query, client)
        score, title, url, snippet = best_result(results, company_name)

        if score < MIN_SCORE:
            skipped.append(company_name)
            print(f"  {'SKIP':<6} {company_name:<28} score {score:.2f} < {MIN_SCORE} — excluded")
            continue

        supplier_id = _next_sup_id(suppliers_data + new_suppliers)
        sup, po, inv, exc_rec, goods_receipt = build_row(
            row_idx, company_name, category, exc_type,
            product_line, supplier_id, rng, snippet, url,
        )
        new_suppliers.append(sup)
        new_invoices.append(inv)
        new_pos.append(po)
        if goods_receipt is not None:
            new_goods_receipts.append(goods_receipt)
        new_exceptions.append(exc_rec)
        existing_names.add(company_name.lower())

        print(f"  {'ADD':<6} {company_name:<28} score {score:.2f}  \"{snippet[:55]}...\"")
        row_idx += 1

    print()

    if not new_exceptions:
        print("No new rows added.")
        return

    save_json(DATA_DIR / "suppliers.json",        suppliers_data + new_suppliers)
    save_json(DATA_DIR / "invoices.json",          invoices_data  + new_invoices)
    save_json(DATA_DIR / "purchase_orders.json",   pos_data       + new_pos)
    save_json(DATA_DIR / "goods_receipts.json",    goods_receipts_data + new_goods_receipts)
    save_json(DATA_DIR / "exception_records.json", exc_data       + new_exceptions)

    types: dict[str, int] = {}
    for e in new_exceptions:
        types[e["exception_type"]] = types.get(e["exception_type"], 0) + 1

    print(f"Added {len(new_exceptions)} rows  (skipped {len(skipped)}: {', '.join(skipped) or 'none'})")
    print(f"Type distribution: {types}")
    print(f"Added matching goods receipts: {len(new_goods_receipts)}")
    print(f"Total suppliers: {len(suppliers_data) + len(new_suppliers)}")


if __name__ == "__main__":
    main()
