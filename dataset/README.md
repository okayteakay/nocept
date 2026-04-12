# Meridian Corp — AP Dataset

Synthetic Accounts Payable dataset for the Invoice Exception Resolution Agent. Every file tells a consistent story: invoices, purchase orders, goods receipts, supplier communications, and pre-classified exceptions that form a coherent narrative across all five exception types.

---

## Files (`data/`)

| File | Records | Description |
|---|---|---|
| `invoices.json` | 213 | Invoices with line items (SKU, qty, unit price, totals) |
| `purchase_orders.json` | 213 | POs with line items; linked to invoices via `po_number` |
| `goods_receipts.json` | 203 | GRNs confirming delivery; linked to PO + invoice |
| `exception_records.json` | 83 | Pre-classified discrepancies with variance amounts and linked comms IDs |
| `historical_approved_exceptions.json` | 67 | Past approved exceptions — Step 3 (historical checker) input |
| `emails.json` | ~104 | Supplier/buyer email threads referencing POs and invoices |
| `phone_transcripts.json` | ~42 | Call transcripts between suppliers and buyers |
| `suppliers.json` | 54 | Supplier master: 12 synthetic (SUP-001–SUP-012) + 42 real companies (SUP-013–SUP-094) |
| `catalog.json` | 1 | Meridian Corp product hierarchy — supplier → product category → grade variants |

---

## Key Relationships

```
purchase_orders ──────────────────< goods_receipts
      │                                    │
      │                po_number ──────────┘
      │
      └──────< invoices ──────< exception_records ──────> emails
                                                    └────> phone_transcripts
```

**Primary join key across all documents:** `po_number`

Invoices additionally carry `invoice_number` and `supplier_id`. Exception records carry `related_email_ids` and `related_transcript_ids` for direct lookups without joins.

---

## Exception Types

| Type | What it means |
|---|---|
| `price_variance` | Invoice unit price differs from the PO unit price |
| `quantity_variance` | Invoiced quantity doesn't match the GRN (goods received) quantity |
| `informal_modification` | A SKU or product grade was substituted without a formal PO amendment |
| `missing_goods_receipt` | No GRN exists for this invoice — delivery unconfirmed |
| `duplicate_invoice` | The same PO has been billed more than once |

Of the 83 exception records, `informal_modification` cases are the richest — they come with linked emails and/or transcripts confirming the substitution, and are the primary target for Steps 4 and 5 of the pipeline.

---

## Line Item Schema

`invoices.json`, `purchase_orders.json`, and `goods_receipts.json` all share a nested `line_items` array:

| Field | Type | Notes |
|---|---|---|
| `sku` | string | Supplier SKU (e.g. `AP-CPA-STD`) |
| `description` | string | Human-readable product name |
| `product_grade` | string | `Standard`, `Premium`, or `Ultra` |
| `unit_price` | float | Per-unit cost in USD |
| `quantity` | int | Units ordered / invoiced / received |
| `total` | float | `unit_price × quantity` (validated within $0.02) |

---

## Suppliers

**Synthetic suppliers (SUP-001–SUP-012):** Meridian Corp's core vendors — paper, medical supplies, IT hardware, industrial equipment, office furniture, chemicals, etc. These appear in the majority of exceptions and have rich associated comms.

**Real companies (SUP-013–SUP-094):** FedEx, Nucor Steel, Eastman Chemical, 3M, Honeywell, and others. These rows exist specifically for **Step 5 validation** — Tavily Search can find genuine public announcements (price increases, product discontinuations, supply shortages) for these companies, making the web research gate meaningful.

---

## Historical Approved Exceptions

`historical_approved_exceptions.json` contains 67 records used by the Step 3 (history checker):

- **55 generated** — randomized approved cases covering all five exception types across all 54 suppliers
- **12 targeted** — hand-crafted gap-fillers that ensure every synthetic supplier has at least one relevant historical record the agent can match against

The history checker matches on: same `supplier_id` + same `exception_type` + variance percentage within 5 percentage points. Approval dates are always before the current invoice date so temporal ordering is preserved.

---

## Catalog

`catalog.json` models Meridian Corp's product hierarchy:

```
Catalog
└── suppliers[]
    └── product_categories[]
        ├── category_name
        ├── standard_sku  /  standard_price
        └── grades[]
            ├── grade (Standard / Premium / Ultra)
            ├── sku
            └── unit_price
```

The catalog is used by the classifier to:
- Detect known grade substitutions (e.g. Standard → Premium)
- Compute expected price deltas for informal modification signals
- Identify whether a new-SKU line item is a known substitute vs. a genuinely unexpected product

---

## Data Generation

All three generators write into `dataset/data/` and are safe to re-run (they overwrite existing files):

```bash
# 1. Full synthetic Meridian Corp dataset
#    Produces invoices, POs, GRNs, exception_records, suppliers, emails, transcripts
uv run python dataset/generate_data.py

# 2. Historical approved exceptions (55 random + 12 targeted)
#    Depends on suppliers.json existing
uv run python dataset/generate_historical_approvals.py

# 3. Real-company rows — requires TAVILY_API_KEY
#    Uses Tavily to verify real suppliers are searchable before inserting
uv run python dataset/generate_real_company_data.py
```

Run them in order: `generate_data.py` → `generate_historical_approvals.py` → `generate_real_company_data.py` (optional).
