# Project Progress

Tracks implementation status against the multi-phase game plan.

**Legend:** ✅ Complete · 🔧 Scaffolded (stub exists, not implemented) · ⬜ Not started

Last updated: 2026-04-11

---

## Infrastructure & Project Setup

- ✅ Python virtual environment + `requirements.txt`
- ✅ `.env.example` with all required variables documented
- ✅ `.gitignore` (excludes `.env`, `data/*.csv`, `__pycache__`, etc.)
- ✅ `README.md` (architecture diagram, setup guide, run commands)
- ✅ Git repository initialized with initial commit
- ✅ `datetime.utcnow()` replaced with timezone-aware `datetime.now(UTC)` throughout

---

## Phase 1 — Data Foundation

### Step 1 — Define data models ✅
- ✅ `models/invoice.py` — `Invoice`, `LineItem` (Pydantic, with line total validator)
- ✅ `models/purchase_order.py` — `PurchaseOrder`, `POLineItem`
- ✅ `models/grn.py` — `GoodsReceiptNote`, `GRNLineItem` (with `is_complete_receipt`)
- ✅ `models/exception.py` — `InvoiceException`, `ExceptionType` enum, `ExceptionState` enum, `LineItemVariance`
- ✅ `models/resolution.py` — `Resolution`, `ResolutionMemo`, `RootCause` enum, `ResolutionAction` enum, `EvidenceItem`

### Step 2 — Generate synthetic exception scenarios 🔧
- 🔧 `ingestion/erp_simulator.py` — all generator stubs in place, none implemented:
  - 🔧 `generate_straight_through_invoice()` — clean match, no exception
  - 🔧 `generate_price_variance_exception()` — 8% overcharge scenario
  - 🔧 `generate_informal_modification_exception()` — canonical Grade A/B paper substitution
  - 🔧 `generate_quantity_variance_exception()` — short shipment scenario
  - 🔧 `generate_missing_receipt_exception()` — invoice with no GRN
  - 🔧 `generate_duplicate_exception()` — re-submitted invoice
  - 🔧 `generate_tax_discrepancy_exception()` — wrong tax amount
  - 🔧 `generate_batch(n, exception_rate)` — mixed batch for demos

### Step 3 — Product catalog and supplier profile dataset ⬜
- ⬜ `data/catalog.json` — SKU grade/tier relationships (e.g. PAPER-A-REAM → PAPER-B-REAM, price premium)
- ⬜ `data/suppliers.json` — 10–15 supplier profiles with product categories
- ⬜ Classifier updated to cross-reference catalog for substitution detection

### Step 4 — Load seed data into Redis ⬜
- ⬜ Script to load POs, invoices, and supplier records as Redis hashes
- ⬜ Exception queue seeded as Redis sorted set (scored by variance × supplier tier)
- ⬜ Verify round-trip query and retrieval

---

## Phase 2 — Exception Detection and Classification Engine

### Step 5 — Three-way matching logic 🔧
- 🔧 `_compute_line_variances()` in `agent/classifier.py` — per-line SKU/qty/price delta
- ⬜ Configurable tolerance comparison (price: 1%/$1, qty: 2%)
- ⬜ Total amount cross-check

### Step 6 — Exception classifier 🔧
- 🔧 `classify_exception()` in `agent/classifier.py` — stub with full decision-tree docstring
- ⬜ Duplicate detection (invoice number exists in Redis for same supplier)
- ⬜ Missing GRN detection
- ⬜ Informal modification / substitution detection (cross-references product catalog)
- ⬜ Quantity variance detection
- ⬜ Price variance detection
- ⬜ Unclassified / escalate fallback
- 🔧 `_detect_informal_modification_signals()` — stub in place

### Step 7 — Write exceptions to Redis queue 🔧
- 🔧 `state/redis_backend.py` — `RedisStateStore` fully stubbed (save, load, transition, list)
- 🔧 `audit/audit_logger.py` — `AuditLogger` + `AuditEvent` stubbed (Redis Streams append)
- ✅ `state/machine.py` — `ExceptionStateMachine` fully implemented, 15/15 tests passing
- ⬜ Redis Streams detection event logged on first ingest
- ⬜ Exception persisted as JSON hash under `exception:<uuid>` key
- ⬜ Supplier index maintained under `supplier:<id>:exceptions`

---

## Phase 3 — Research and Resolution Engine

### Step 8 — Tavily research module 🔧
- 🔧 `clients/tavily_client.py` — `TavilyClient` wrapper stubbed (search, supplier context, product availability, price changes)
- 🔧 `agent/researcher.py` — `research_exception()`, `_build_search_queries()`, `_score_relevance()`, `_summarize_findings()` all stubbed
- ⬜ Query construction per exception type (price variance → surcharge queries; informal mod → substitution/shortage queries)
- ⬜ Relevance scoring against supplier name and SKU keywords
- ⬜ Top 3–5 findings stored in exception record in Redis

### Step 9 — Historical pattern matcher 🔧
- 🔧 `agent/context_retriever.py` — `retrieve_supplier_context()`, `_extract_substitution_patterns()`, `_compute_average_price_uplift()` stubbed
- ⬜ Query Redis for all resolved exceptions by supplier + product category
- ⬜ Substitution pattern aggregation (from_sku → to_sku counts)
- ⬜ Confidence score output (≥80% match → high confidence)

### Step 10 — Resolution engine 🔧
- 🔧 `agent/rules_engine.py` — `apply_rules()`, `_within_tolerance()`, `_is_known_substitution_pattern()`, `_research_corroborates()` stubbed
- ⬜ Rule 1: Duplicate → AUTO_REJECT
- ⬜ Rule 2: Within tolerance → AUTO_APPROVE (POLICY_COMPLIANT_VARIANCE)
- ⬜ Rule 3: Known Redis pattern + research corroboration → AUTO_APPROVE (UNDOCUMENTED_MODIFICATION)
- ⬜ Rule 4: Research alone corroborates → AUTO_APPROVE with memo
- ⬜ Rule 5: Exceeds tolerance, no evidence → ESCALATE_TO_HUMAN
- ⬜ Informal modification special path: catalog-verified grade upgrade + price within 5% → auto-resolve with "catalog-verified substitute" memo

### Step 11 — Resolution memo generator 🔧
- 🔧 `agent/memo_generator.py` — `generate_memo()`, `_format_evidence_items()`, `_write_summary()` stubbed
- ⬜ Exception summary section (PO vs invoice comparison table)
- ⬜ Root cause categorization with plain-English explanation
- ⬜ Evidence citations (Tavily URLs + Redis history)
- ⬜ Recommended action + confidence level
- ⬜ Approval status field

---

## Phase 4 — Orchestration with watsonx Orchestrate

### Step 12 — Define watsonx Orchestrate skills ⬜
- ⬜ Skill 1: Exception Intake (three-way match + classify)
- ⬜ Skill 2: Historical Lookup (Redis pattern query)
- ⬜ Skill 3: External Research (Tavily queries)
- ⬜ Skill 4: Resolution Decision (rules engine)
- ⬜ Skill 5: Memo Generation
- ⬜ Skill 6: State Update (Redis persist + audit)

### Step 13 — Build the orchestration flow 🔧
- 🔧 `agent/pipeline.py` — `run_pipeline()` stub with full workflow documented in docstring
- ⬜ Sequential pipeline: Intake → Classify → (History + Research in parallel) → Decision → Memo → State Update
- ⬜ Branching: Duplicate/Missing GRN → skip research, fast path
- ⬜ Informal modification branch: tailored Tavily queries

### Step 14 — End-to-end orchestration test ⬜
- ⬜ Run 5–10 synthetic exceptions through the full pipeline
- ⬜ Verify Redis state transitions: received → triaged → researching → resolved/escalated
- ⬜ Verify audit trail captured in Redis Streams
- ⬜ Verify resolution memos are coherent

---

## Phase 5 — Dashboard and Demo Layer

### Step 15 — Streamlit dashboard 🔧
- 🔧 `dashboard/app.py` — 4-page shell with resource init and routing
  - 🔧 Page 1: Exception Queue — table stub (`load_exception_queue`, `render_exception_queue`)
  - 🔧 Page 2: Resolution Detail — detail panel stub (`render_resolution_detail`)
  - 🔧 Page 3: Spend Variance Report — chart + table stub (`render_spend_variance`)
  - 🔧 Page 4: Audit Trail — event log stub (`render_audit_trail`)
- ⬜ Exception queue color-coding by status (red/yellow/green/orange)
- ⬜ "Suspected Informal Modification" prominent filter
- ⬜ PO vs invoice comparison table with delta highlighting
- ⬜ Sidebar aggregate metrics (auto-resolution rate, avg resolution time, total variance)
- ⬜ "Undocumented Modification Variance" metric (dollar total)
- ⬜ Spend variance chart (by supplier and category over time)

### Step 16 — Demo trigger ⬜
- ⬜ Button or input to submit a new invoice from the synthetic dataset
- ⬜ Real-time pipeline progress visible in dashboard

---

## Phase 6 — Testing, Hardening, and Demo Prep

### Step 17 — Full dataset pipeline run ⬜
- ⬜ All 30–50 synthetic pairs processed
- ⬜ Every resolution memo reviewed for coherence
- ⬜ Informal modification classification accuracy verified
- ⬜ Spend variance report aggregation verified

### Step 18 — Demo scenario selection ⬜
- ⬜ Scenario A: Price variance auto-resolved via Tavily supplier announcement
- ⬜ Scenario B: Grade A/B substitution resolved via catalog + Redis history
- ⬜ Scenario C: Complex exception correctly escalated with assembled research
- ⬜ Scenario D: Duplicate invoice caught instantly

### Step 19 — Pitch deck tie-in ⬜
- ⬜ Dashboard analytics show cost savings per exception
- ⬜ Time savings metric visible (3–7 days → minutes)
- ⬜ Dollar value of undocumented modifications surfaced
- ⬜ Numbers on screen during demo match business case in pitch

---

## Test Suite Status

| Test File | Stubs | Passing |
|-----------|-------|---------|
| `test_state_machine.py` | 15 | ✅ 15/15 |
| `test_classifier.py` | 16 | ⬜ 0/16 |
| `test_context_retriever.py` | 11 | ⬜ 0/11 |
| `test_researcher.py` | 12 | ⬜ 0/12 |
| `test_rules_engine.py` | 13 | ⬜ 0/13 |
| `test_memo_generator.py` | 11 | ⬜ 0/11 |
| `test_pipeline.py` | 12 | ⬜ 0/12 |
| `test_ingestion.py` | 18 | ⬜ 0/18 |
| **Total** | **108** | **15/108** |

---

## Exception Type Coverage

| Exception Type | Classifier | Rules Engine | Researcher | ERP Simulator | Tests |
|----------------|-----------|--------------|------------|---------------|-------|
| Price Variance | 🔧 | 🔧 | 🔧 | 🔧 | ⬜ |
| Quantity Variance | 🔧 | 🔧 | 🔧 | 🔧 | ⬜ |
| Missing Receipt | 🔧 | 🔧 | 🔧 | 🔧 | ⬜ |
| Duplicate | 🔧 | 🔧 | N/A | 🔧 | ⬜ |
| Tax Discrepancy | 🔧 | 🔧 | 🔧 | 🔧 | ⬜ |
| Freight Discrepancy | 🔧 | 🔧 | 🔧 | ⬜ | ⬜ |
| Currency Conversion | 🔧 | 🔧 | 🔧 | ⬜ | ⬜ |
| Informal Modification | 🔧 | 🔧 | 🔧 | 🔧 | ⬜ |
