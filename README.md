# Nocept — Autonomous Invoice Exception Resolution Agent

> An AI-powered agent that autonomously triages and resolves invoice-to-PO mismatches in enterprise Accounts Payable workflows. Built for Meridian Corp's AP team — handles price variances, quantity discrepancies, product substitutions, missing goods receipts, and duplicate invoices through a deterministic six-step pipeline, approving or escalating each case without human intervention where possible.

---

## The Problem

Enterprise AP teams spend thousands of hours annually manually reviewing invoices that don't match their purchase orders. A three-way match failure — invoice vs. PO vs. goods receipt — triggers an exception that requires human investigation:

- *Did the supplier substitute a product without updating the PO?*
- *Is this a known price increase we agreed to verbally?*
- *Was this invoice already submitted last month?*

Most of these exceptions have clear answers buried in email threads, call transcripts, and prior approval records. Nocept finds those answers automatically.

---

## How It Works

Every incoming invoice runs through six gates in sequence. The **first gate that fires** determines the outcome — no later gates are evaluated.

```
Invoice + PO + GRN
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 1 │ CLASSIFY                                                │
│         │ Detect exception type(s) and compute variance amount    │
│         │ ─ Duplicate?  →  AUTO_REJECT  (skip all gates)          │
│         │ ─ No exception?  →  AUTO_APPROVE  (straight-through)    │
└─────────┼─────────────────────────────────────────────────────────┘
          │ exception detected
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 2 │ TOLERANCE CHECK                                         │
│         │ Is the invoice-to-PO variance ≤ 1%?                     │
│         │ Yes  →  AUTO_APPROVE  (confidence 1.0)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 3 │ HISTORICAL PRECEDENT                                    │
│         │ Was a similar exception approved for this supplier       │
│         │ within the past 5 percentage points of variance?         │
│         │ Yes  →  AUTO_APPROVE  (confidence 0.90)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 4 │ COMMUNICATIONS                                          │
│         │ Does a linked email or call transcript confirm the       │
│         │ exception? (LLM-evaluated, threshold 0.75)               │
│         │ Yes  →  AUTO_APPROVE  (confidence 0.85)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 5 │ WEB RESEARCH                                            │
│         │ Does a Tavily web search find a public source            │
│         │ corroborating the exception? (threshold 0.70)            │
│         │ Yes  →  AUTO_APPROVE  (confidence 0.80)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Step 6 │ ESCALATE                                                │
│         │ No gate fired — route to human reviewer                  │
│         │  →  ESCALATE_TO_HUMAN                                    │
└───────────────────────────────────────────────────────────────────┘
          │
          ▼
  Resolution Memo generated  →  persisted to Redis  →  audit trail written
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Ingestion Layer                             │
│                                                                      │
│   JSON Dataset (213 invoices)   │   ERP Simulator   │   Webhook*    │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Agent Pipeline                               │
│                                                                      │
│   classifier.py       →  exception type + variance detection         │
│   rules_engine.py     →  six deterministic decision gates            │
│   history_checker.py  →  historical approved-cases JSON lookup       │
│   comms_checker.py    →  OpenAI-compatible LLM + fallback heuristics │
│   researcher.py       →  Tavily Search API + evidence scoring        │
│   context_retriever.py→  supplier pattern summary from Redis         │
│   memo_generator.py   →  structured ResolutionMemo assembly          │
│   pipeline.py         →  end-to-end orchestrator                     │
│                                                                      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
              ┌──────────────┼───────────────┐
              ▼              ▼               ▼
   ┌────────────────┐  ┌──────────────┐  ┌────────────────────────┐
   │  Redis Stack   │  │  Audit Trail │  │   Knowledge Base       │
   │                │  │              │  │                        │
   │  State store   │  │  Redis       │  │  Vector search         │
   │  Queue index   │  │  Streams     │  │  (emails, transcripts) │
   │  Supplier idx  │  │  (append-    │  │  Resolution history    │
   │  State index   │  │   only)      │  │  (structured + vector) │
   └────────────────┘  └──────────────┘  └────────────────────────┘
              │
   ┌──────────┴───────────┐
   ▼                      ▼
┌──────────────┐   ┌──────────────────────────────┐
│  Dashboard   │   │       Orchestrate API         │
│  (Streamlit) │   │       (FastAPI / REST)         │
│              │   │                              │
│  Live queue  │   │  POST /tools/intake          │
│  Audit trail │   │  GET  /tools/tolerance/{id}  │
│  Memo viewer │   │  GET  /tools/history/{id}    │
│  KB search   │   │  GET  /tools/communications/{id} │
└──────────────┘   │  POST /tools/research/{id}   │
                   │  POST /tools/resolve/{id}    │
                   │                              │
                   │  POST /kb/search/emails      │
                   │  POST /kb/search/transcripts │
                   │  GET  /kb/history/{supplier} │
                   └──────────────────────────────┘
```

*Webhook handler is scaffolded but not yet wired to a live ERP.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| Web framework | FastAPI + Uvicorn |
| Dashboard | Streamlit |
| State & queue | Redis Stack (with Search module) |
| Audit trail | Redis Streams (append-only, SOX-compliant) |
| Vector search | Redis Stack + `sentence-transformers` (`all-MiniLM-L6-v2`, 384-d) |
| Step 4 — comms LLM | OpenAI-compatible API (nanogpt by default, or standard OpenAI) |
| Step 5 — web research | Tavily Search API |
| Data validation | Pydantic v2 |
| Config | `pydantic-settings` (reads `.env`) |
| Package manager | `uv` |

---

## Project Structure

```
nocept/
│
├── agent/                          # Core decision pipeline
│   ├── pipeline.py                 # End-to-end orchestrator — main entry point
│   ├── classifier.py               # Step 1 — three-way match, variance detection
│   ├── rules_engine.py             # All six decision gates + RulesDecision model
│   ├── history_checker.py          # Step 3 — historical precedent lookup
│   ├── comms_checker.py            # Step 4 — LLM + fallback keyword analysis
│   ├── researcher.py               # Step 5 — Tavily queries + evidence scoring
│   ├── context_retriever.py        # Supplier pattern context from Redis
│   └── memo_generator.py           # Resolution memo assembly
│
├── orchestrate/
│   └── api.py                      # FastAPI — six REST tools for watsonx Orchestrate
│
├── models/                         # Pydantic data models
│   ├── invoice.py                  # Invoice + LineItem
│   ├── purchase_order.py           # PurchaseOrder
│   ├── grn.py                      # GoodsReceiptNote
│   ├── exception.py                # InvoiceException, ExceptionState, LineItemVariance
│   ├── exception_record.py         # ExceptionRecord, ExceptionType enum
│   ├── resolution.py               # Resolution, ResolutionMemo, ResolutionAction, RootCause
│   ├── communication.py            # Email, PhoneTranscript
│   └── supplier.py                 # Supplier, SupplierWithCatalog, ProductGrade, Catalog
│
├── state/
│   ├── machine.py                  # ExceptionStateMachine — enforces valid transitions
│   └── redis_backend.py            # RedisStateStore — CRUD, indexes, queue helpers
│
├── knowledge/                      # Redis-backed knowledge base
│   ├── client.py                   # KnowledgeBaseClient — unified facade
│   ├── embedder.py                 # Sentence-transformer embeddings (L2-normalized)
│   ├── email_store.py              # EmailVectorStore — Redis vector index
│   ├── transcript_store.py         # TranscriptVectorStore — Redis vector index
│   ├── resolution_store.py         # ResolutionHistoryStore — structured lookups
│   └── seeder.py                   # Upserts dataset into KB at startup
│
├── audit/
│   └── audit_logger.py             # AuditLogger — writes AuditEvents to Redis Streams
│
├── clients/
│   ├── redis_client.py             # Connection factory + RedisStreamsClient
│   └── tavily_client.py            # TavilyClient — search, supplier context, price changes
│
├── ingestion/
│   ├── json_ingestor.py            # DatasetBundle — loads and cross-links all JSON data
│   ├── erp_simulator.py            # Generates synthetic invoice/PO/GRN tuples for demos
│   └── webhook_handler.py          # FastAPI webhook stubs (ERP integration, scaffolded)
│
├── config/
│   └── settings.py                 # AppConfig (pydantic-settings, .env-backed, cached)
│
├── dataset/
│   ├── data/                       # Seven JSON data files — Meridian Corp AP dataset
│   ├── generate_data.py            # Synthetic dataset generator
│   ├── generate_historical_approvals.py  # Historical approved exceptions generator
│   └── generate_real_company_data.py     # Real-company rows for Tavily validation
│
├── dashboard/
│   └── app.py                      # Streamlit UI — queue, audit trail, memo viewer, KB search
│
├── tests/                          # Pytest suite
│   ├── conftest.py                 # Shared fixtures (fakeredis, models, mocked clients)
│   ├── test_classifier.py
│   ├── test_rules_engine.py
│   ├── test_history_checker.py
│   ├── test_context_retriever.py
│   ├── test_memo_generator.py
│   ├── test_researcher.py          # Includes live Tavily integration tests (auto-skipped without key)
│   ├── test_comms_checker.py
│   ├── test_state_machine.py
│   └── test_pipeline.py
│
├── run_demo.py                     # End-to-end demo script with KB search output
├── docker-compose.yml              # Redis Stack container (port 6379 + RedisInsight on 8001)
├── pyproject.toml
└── .env.example
```

---

## Setup

### Prerequisites

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) — fast Python package manager
- [Docker](https://docs.docker.com/get-docker/) — for Redis Stack
- A [Tavily API key](https://app.tavily.com) — for Step 5 web research
- An OpenAI-compatible API key — for Step 4 comms analysis (nanogpt or standard OpenAI)

### 1 — Clone & install

```bash
git clone https://github.com/okayteakay/nocept.git
cd nocept
uv sync
```

### 2 — Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required
REDIS_URL=redis://localhost:6379/0
TAVILY_API_KEY=your-tavily-key
OPENAI_API_KEY=your-openai-or-nanogpt-key

# Optional — leave blank for standard OpenAI
OPENAI_BASE_URL=https://nano-gpt.com/api/v1
OPENAI_MODEL=gpt-4o-mini

# Business rules — defaults shown
PRICE_TOLERANCE_PCT=0.01    # Step 2: auto-approve if variance ≤ 1%
QTY_TOLERANCE_PCT=0.02      # Classifier: flag quantity delta > 2%
LOG_LEVEL=INFO
```

### 3 — Start Redis Stack

```bash
docker compose up -d
```

This starts Redis Stack on port `6379` (API-compatible with plain Redis) and RedisInsight on `http://localhost:8001` for browsing keys.

---

## Running

### Orchestrate API

Exposes the six pipeline steps as REST endpoints. Import `/openapi.json` into IBM watsonx Orchestrate to register them as tools automatically.

```bash
uvicorn orchestrate.api:app --reload --port 8000
```

| Method | Endpoint | Step |
|---|---|---|
| `POST` | `/tools/intake` | 1 — Classify, detect exception types, persist |
| `GET` | `/tools/tolerance/{id}` | 2 — Auto-approve if variance ≤ 1% |
| `GET` | `/tools/history/{id}` | 3 — Auto-approve on historical precedent |
| `GET` | `/tools/communications/{id}` | 4 — Auto-approve on comms confirmation |
| `POST` | `/tools/research/{id}` | 5 — Auto-approve on web research |
| `POST` | `/tools/resolve/{id}` | 6 — Finalize, generate memo, write resolution |
| `POST` | `/kb/search/emails` | Semantic search over indexed emails |
| `POST` | `/kb/search/transcripts` | Semantic search over indexed transcripts |
| `GET` | `/kb/history/{supplier_id}` | Supplier resolution history summary |

Interactive docs: `http://localhost:8000/docs`

### Dashboard

```bash
streamlit run dashboard/app.py
```

Provides a live view of the exception queue, per-exception audit trail, resolution memo viewer, and knowledge base search.

### End-to-end demo

```bash
uv run python run_demo.py
```

Generates a synthetic informal-modification exception, runs the full pipeline, prints the resolution memo, and demonstrates knowledge base search.

### Run the pipeline programmatically

```python
from ingestion.json_ingestor import load_dataset
from agent.pipeline import run_pipeline
from config.settings import get_settings
from clients.redis_client import get_redis_connection, RedisStreamsClient
from clients.tavily_client import TavilyClient
from state.redis_backend import RedisStateStore
from audit.audit_logger import AuditLogger

config = get_settings()
r = get_redis_connection(config.redis_url)

dataset = load_dataset()
for invoice, po, grn, exc_record in dataset.exception_triples():
    result = run_pipeline(
        invoice, po, grn,
        store=RedisStateStore(r),
        tavily=TavilyClient(config.tavily_api_key),
        audit=AuditLogger(RedisStreamsClient(r, "ap:audit:events")),
        config=config,
    )
    print(result.resolution.final_state, result.resolution.memo.summary)
```

### Tests

```bash
uv run pytest tests/ -v
```

Live Tavily tests in `TestTavilyLive` are automatically skipped unless `TAVILY_API_KEY` is set in the environment:

```bash
TAVILY_API_KEY=your-key uv run pytest tests/test_researcher.py::TestTavilyLive -v
```

### Regenerate dataset

```bash
# Full synthetic Meridian Corp dataset (invoices, POs, GRNs, exceptions, comms)
uv run python dataset/generate_data.py

# Historical approved exceptions (55 random + 12 targeted gap-fillers)
uv run python dataset/generate_historical_approvals.py

# Real-company rows (FedEx, Nucor, 3M, etc.) for Step 5 Tavily validation
# Requires TAVILY_API_KEY
uv run python dataset/generate_real_company_data.py
```

---

## Dataset

The `dataset/data/` directory is a fully synthetic Meridian Corp AP dataset — consistent invoices, purchase orders, goods receipts, supplier emails, and call transcripts that form a coherent narrative for each exception type.

| File | Records | Description |
|---|---|---|
| `invoices.json` | 213 | Invoices with line items (SKU, qty, unit price, totals) |
| `purchase_orders.json` | 213 | Matching POs linked via `po_number` |
| `goods_receipts.json` | 203 | GRNs confirming delivery; 10 invoices have no matching GRN (→ `missing_goods_receipt`) |
| `exception_records.json` | 83 | Pre-classified exceptions with variance amounts and linked communication IDs |
| `historical_approved_exceptions.json` | 67 | Past approved exceptions used by Step 3 (55 generated + 12 targeted) |
| `emails.json` | ~104 | Supplier/buyer email threads referencing POs and invoices |
| `phone_transcripts.json` | ~42 | Call transcripts between suppliers and buyers |
| `suppliers.json` | 54 | Supplier master: 12 synthetic + 42 real companies (SUP-013–SUP-094) |
| `catalog.json` | 1 | Meridian Corp product hierarchy — supplier → category → grade variants |

**Exception type breakdown:**

| Exception Type | Meaning |
|---|---|
| `price_variance` | Invoice unit price differs from PO |
| `quantity_variance` | Invoiced quantity doesn't match GRN quantity |
| `informal_modification` | SKU or product grade substituted without a formal PO amendment |
| `missing_goods_receipt` | No GRN exists for this invoice |
| `duplicate_invoice` | Same PO billed more than once |

**Key relationships:**

```
purchase_orders ──< goods_receipts
       │
       └──── invoices ──< exception_records ──> emails
                                              └─> phone_transcripts
```

Join key across all documents: `po_number`. Invoices also carry `invoice_number` and `supplier_id`.

Real-company rows (FedEx, Nucor, Eastman Chemical, 3M, etc.) are included specifically for Step 5 validation — Tavily can find public pricing announcements and product discontinuation notices for these companies.

---

## Exception Lifecycle

Each exception follows a strict state machine:

```
RECEIVED → TRIAGED → RESEARCHING → PENDING_APPROVAL → RESOLVED
                  ↘                               ↗
                   ──────────────────────────────→ ESCALATED
```

State transitions are enforced by `ExceptionStateMachine` and persisted atomically to Redis. Every transition is written to the append-only Redis Streams audit trail.

---

## Knowledge Base

On startup, the Orchestrate API seeds a Redis Stack knowledge base with all historical resolutions, emails, and transcripts from the dataset. This powers:

- **Semantic email search** (`POST /kb/search/emails`) — find emails by meaning, not just keywords, with optional date/PO/invoice filters
- **Semantic transcript search** (`POST /kb/search/transcripts`) — same for call transcripts
- **Supplier resolution history** (`GET /kb/history/{supplier_id}`) — aggregate stats: total exceptions, resolution rate, most common types, average variance

Embeddings use `all-MiniLM-L6-v2` (384 dimensions, ~90 MB, downloaded from HuggingFace on first run and cached). Vectors are L2-normalized for cosine similarity over Redis HNSW indexes.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis Stack connection string |
| `TAVILY_API_KEY` | — | Tavily Search API key (Step 5) |
| `OPENAI_API_KEY` | — | OpenAI-compatible key (Step 4 LLM) |
| `OPENAI_BASE_URL` | — | Custom endpoint e.g. nanogpt — leave blank for standard OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name for communications analysis |
| `PRICE_TOLERANCE_PCT` | `0.01` | Step 2 auto-approve threshold (1%) |
| `QTY_TOLERANCE_PCT` | `0.02` | Classifier quantity variance threshold (2%) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model for KB vector search |
| `VECTOR_DIMENSIONS` | `384` | Output dimensions of the embedding model |
| `VECTOR_INDEX_PREFIX` | `kb:` | Redis key namespace for all knowledge base entries |
| `LOG_LEVEL` | `INFO` | Python logging level |
