# Data Directory

This directory is managed by a separate data generation team. Drop the generated CSV files here and point `ingestion/csv_ingestor.py` at them.

**Do not commit data files.** The `.gitignore` excludes `*.csv`, `*.json`, `*.parquet`, and `*.xlsx` from this directory.

---

## Expected Files

### `invoices.csv`

One row per line item. Multiple rows share the same `invoice_id` when an invoice has multiple line items.

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `invoice_id` | string | `INV-2024-00123` | Unique per invoice |
| `supplier_id` | string | `SUPP-0042` | Foreign key to supplier master |
| `supplier_name` | string | `Acme Paper Co.` | |
| `po_number` | string | `PO-2024-00456` | Links to `purchase_orders.csv` |
| `invoice_date` | date (YYYY-MM-DD) | `2024-03-15` | |
| `currency` | string (ISO 4217) | `USD` | |
| `sku` | string | `PAPER-A-REAM` | Supplier SKU |
| `description` | string | `Grade A Office Paper, 500-sheet ream` | |
| `quantity` | decimal | `450` | |
| `unit_price` | decimal | `50.00` | |
| `line_total` | decimal | `22500.00` | quantity × unit_price |
| `unit_of_measure` | string | `EA` | EA, CASE, KG, etc. |
| `tax_amount` | decimal | `0.00` | Invoice-level; repeat same value per row |
| `freight_amount` | decimal | `125.00` | Invoice-level; repeat same value per row |
| `total_amount` | decimal | `26625.00` | Invoice-level; repeat same value per row |

---

### `purchase_orders.csv`

One row per line item. Same structure as `invoices.csv` with `po_number` as the primary key.

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `po_number` | string | `PO-2024-00456` | Unique per PO |
| `supplier_id` | string | `SUPP-0042` | |
| `supplier_name` | string | `Acme Paper Co.` | |
| `buyer_id` | string | `BUYER-007` | Internal buyer/requestor |
| `created_date` | date (YYYY-MM-DD) | `2024-03-01` | |
| `currency` | string (ISO 4217) | `USD` | |
| `sku` | string | `PAPER-A-REAM` | |
| `description` | string | `Grade A Office Paper, 500-sheet ream` | |
| `quantity` | decimal | `500` | Ordered quantity |
| `unit_price` | decimal | `50.00` | |
| `line_total` | decimal | `25000.00` | |
| `unit_of_measure` | string | `EA` | |
| `tax_amount` | decimal | `0.00` | PO-level |
| `freight_amount` | decimal | `0.00` | PO-level (often 0 at order time) |
| `total_amount` | decimal | `25000.00` | PO-level |

---

### `grns.csv`

One row per line item received. `grn_id` groups rows for the same delivery.

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `grn_id` | string | `GRN-2024-00789` | Unique per receipt |
| `po_number` | string | `PO-2024-00456` | Links to `purchase_orders.csv` |
| `supplier_id` | string | `SUPP-0042` | |
| `receipt_date` | date (YYYY-MM-DD) | `2024-03-14` | |
| `sku` | string | `PAPER-A-REAM` | May differ from PO SKU on substitution |
| `quantity_received` | decimal | `450` | |
| `condition` | string | `acceptable` | `acceptable`, `damaged`, `rejected` |

---

## Informal Modification Scenario Example

The canonical scenario the agent is designed to detect:

**PO** (`purchase_orders.csv`):
```
PO-001, SUPP-042, Acme Paper, ..., PAPER-A-REAM, Grade A Paper, 500, 50.00, 25000.00, ...
```

**Invoice** (`invoices.csv`) — two line items:
```
INV-001, SUPP-042, Acme Paper, PO-001, ..., PAPER-A-REAM, Grade A Paper, 450, 50.00, 22500.00, ...
INV-001, SUPP-042, Acme Paper, PO-001, ..., PAPER-B-REAM, Grade B Paper, 50,  80.00,  4000.00, ...
```

**GRN** (`grns.csv`) — matches invoice (not PO):
```
GRN-001, PO-001, SUPP-042, ..., PAPER-A-REAM, 450, acceptable
GRN-001, PO-001, SUPP-042, ..., PAPER-B-REAM, 50,  acceptable
```

Three-way match fails: invoice total $26,500 vs PO $25,000, and a new SKU appears on the invoice with no PO line.

---

## Matching Logic

`csv_ingestor.py` matches documents by `po_number`:
- Each invoice row is grouped by `invoice_id` → one `Invoice` object with multiple `LineItem`s
- Each PO row is grouped by `po_number` → one `PurchaseOrder` object
- GRN rows are grouped by `grn_id` → one `GoodsReceiptNote` object, matched to the PO via `po_number`
- Invoices with no matching PO are flagged at ingest time as `ExceptionType.UNKNOWN`
- Invoices with no matching GRN are passed to the pipeline with `grn=None` (triggers `MISSING_RECEIPT` classification)
