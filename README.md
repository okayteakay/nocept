# ReceiptFinder — Autonomous Invoice Exception Resolution Agent

> A lightweight, self-contained LLM-driven AP agent that ingests invoices, POs, and GRNs via a unified REST endpoint, runs each through a deterministic decision pipeline, and either auto-resolves or escalates to a human reviewer. Built for Meridian Corp's AP team.

**v5.0.0 — Debloated edition.** Removed Celery, knowledge base, analytics, auth, notifications, dashboard, and web research. The pipeline now runs in-process via FastAPI `BackgroundTasks`. Document ingestion unified via LLM-powered normalizer (no SAP mapper, no Tesseract). Single docker-compose stack: `redis` + `api` only.

---

## The Problem

Enterprise AP teams spend thousands of hours annually manually reviewing invoices that don't match their purchase orders. A three-way match failure — invoice vs. PO vs. goods receipt — triggers an exception that requires human investigation:

- *Did the supplier substitute a product without updating the PO?*
- *Is this a known price increase we agreed to verbally?*
- *Was this invoice already submitted last month?*

Most of these exceptions have clear answers in email threads, call transcripts, and prior approval records. ReceiptFinder finds those answers automatically.

---

## How It Works

Every incoming invoice runs through four deterministic gates in sequence. The **first gate that fires** determines the outcome.

```
Invoice + PO + GRN
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│  Gate 1 │ CLASSIFY & DUPLICATE CHECK                              │
│         │ Detect exception type(s), compute variance, check        │
│         │ for duplicates in Redis history.                         │
│         │ ─ Duplicate?  →  ESCALATE_TO_HUMAN                      │
│         │ ─ No exception?  →  AUTO_APPROVE  (straight-through)    │
└─────────┼─────────────────────────────────────────────────────────┘
          │ exception detected (not a duplicate)
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Gate 2 │ TOLERANCE CHECK                                         │
│         │ Is the invoice-to-PO variance ≤ threshold?               │
│         │ Yes  →  AUTO_APPROVE  (confidence 1.0)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Gate 3 │ HISTORICAL PRECEDENT                                    │
│         │ Was a similar exception approved for this supplier       │
│         │ within the past 5 percentage points of variance?         │
│         │ Yes  →  AUTO_APPROVE  (confidence 0.90)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Gate 4 │ COMMUNICATIONS                                          │
│         │ Does a linked email or call transcript confirm the       │
│         │ exception? (LLM-evaluated, threshold 0.75)               │
│         │ Yes  →  AUTO_APPROVE  (confidence 0.85)                  │
└─────────┼─────────────────────────────────────────────────────────┘
          │
          ▼
  No gate fired — ESCALATE_TO_HUMAN for human review
          │
          ▼
  Resolution Memo generated  →  persisted to Redis  →  audit trail written
```

Each gate is a pure function in [agent/](agent/); the orchestrator that wires them together is [agent/langgraph_agent.py](agent/langgraph_agent.py). The pipeline runs in-process via FastAPI `BackgroundTasks`, not via a separate Celery worker.

---

## Architecture

```
                    Invoice / PO / GRN (JSON/text/image/PDF)
                             │
                             ▼
                  ┌─────────────────────────┐
                  │  POST /ingest           │   orchestrate/api.py
                  │  Unified LLM normalizer │   ingestion/normalizer.py
                  │  Accepts: json|text|    │
                  │          image|pdf      │
                  └────────────┬────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
         ▼ (PO/GRN)                  ▼ (Invoice)
      Cache in                   Create exception
      Redis 30d                  Run pipeline in
      TTL + audit                background task
                                 (BackgroundTasks)
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │  LangGraph Pipeline      │
                        │  (in-process, async)     │
                        │                          │
                        │  Four-gate decision:     │
                        │   classify →             │
                        │   tolerance →            │
                        │   history →              │
                        │   comms →                │
                        │   generate_memo →        │
                        │   persist                │
                        └──────────────┬───────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    │                                     │
                    ▼                                     ▼
         ┌─────────────────────┐           ┌──────────────────────────┐
         │  Redis Stack :6379  │           │  orchestrate/api.py      │
         │                     │           │                          │
         │  state store        │           │  /exceptions/list        │
         │  audit trail        │           │  /tools/approve/{id}     │
         │  queue index        │           │  /tools/reject/{id}      │
         │  PO/GRN cache       │           │  /health                 │
         └─────────────────────┘           └──────────────────────────┘
                                                        ▲
                                                        │
                                            Human reviewer / client
```

**Ingestion flow:**
1. **PO**: Parsed by normalizer → cached in Redis under `po:<po_number>` (30-day TTL) + audited
2. **GRN**: Parsed by normalizer → cached under `grn:<po_number>` + check for any `MISSING_GOODS_RECEIPT` exceptions on same PO and re-trigger them
3. **Invoice**: Parsed by normalizer → look up cached PO (422 if missing) → create `InvoiceException` → save to state store → enqueue background pipeline task (202 Accepted)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Agent framework | **LangGraph** (in-process state machine) |
| Async execution | **FastAPI BackgroundTasks** |
| Web framework | FastAPI + Uvicorn |
| State & queue | **Redis** (state store, audit trail, cache) |
| Audit trail | Redis Streams (append-only, immutable) |
| Document normalization | OpenAI-compatible LLM (vision-capable for images/PDFs) + Pydantic validation |
| Step 4 — comms LLM | OpenAI-compatible API (gpt-4o-mini or similar) |
| OCR/PDF | `pdf2image` + `Pillow` (no Tesseract) |
| Data validation | Pydantic v2 |
| Config | `pydantic-settings` (reads `.env`) |
| Container | Docker + Docker Compose (2 services) |

---

## Project Structure

```
receiptfinder/
│
├── agent/                          # Core decision pipeline
│   ├── langgraph_agent.py          # In-process LangGraph orchestrator
│   ├── classifier.py               # Gate 1 — three-way match, variance detection
│   ├── rules_engine.py             # All four decision gates + RulesDecision model
│   ├── history_checker.py          # Gate 3 — historical precedent lookup
│   ├── comms_checker.py            # Gate 4 — LLM + fallback keyword analysis
│   ├── context_retriever.py        # Supplier pattern context from Redis
│   └── memo_generator.py           # Resolution memo assembly
│
├── orchestrate/
│   ├── api.py                      # FastAPI: unified /ingest, approvals, dashboard, health
│   └── agent_prompt.md             # System prompt (reference)
│
├── models/                         # Pydantic data models
│   ├── invoice.py                  # Invoice + LineItem
│   ├── purchase_order.py           # PurchaseOrder
│   ├── grn.py                      # GoodsReceiptNote
│   ├── exception.py                # InvoiceException, ExceptionState, LineItemVariance
│   ├── exception_record.py         # ExceptionRecord, ExceptionType enum
│   ├── resolution.py               # Resolution, ResolutionMemo, ResolutionAction, RootCause
│   └── communication.py            # Email, PhoneTranscript
│
├── state/
│   ├── machine.py                  # ExceptionStateMachine — enforces valid transitions
│   └── redis_backend.py            # RedisStateStore — CRUD, indexes, queue helpers
│
├── audit/
│   └── audit_logger.py             # AuditLogger — writes AuditEvents to Redis Streams
│
├── clients/
│   └── redis_client.py             # Connection factory + RedisStreamsClient
│
├── ingestion/                      # Inbound data processing
│   ├── normalizer.py               # LLM-powered doc normalizer (invoice|po|grn, json|text|image|pdf)
│   ├── llm_extract.py              # Legacy LLM extraction (kept for reference)
│   ├── ocr.py                      # Legacy OCR (kept for reference)
│   └── webhook_handler.py          # Legacy SAP webhook (kept for reference)
│
├── config/
│   └── settings.py                 # AppConfig (pydantic-settings, .env-backed, cached)
│
├── tests/                          # Pytest suite
│   ├── conftest.py                 # Shared fixtures
│   ├── test_classifier.py
│   ├── test_state_machine.py
│   ├── test_approval_workflow.py
│   ├── test_duplicate_detection.py
│   ├── test_memo_generator.py
│   ├── test_error_handling.py
│   └── test_context_retriever.py
│
├── docker-compose.yml              # 2 services: redis + api
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Setup

### Prerequisites

- Python 3.11+
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose
- An OpenAI-compatible API key (must be **vision-capable** for image/PDF parsing — e.g., `gpt-4o-mini`)

### 1 — Clone & install

```bash
git clone https://github.com/meridian-ap/receiptfinder.git
cd receiptfinder
pip install -e .
```

Or with `uv`:
```bash
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
OPENAI_API_KEY=sk-...  # Must be vision-capable model
OPENAI_MODEL=gpt-4o-mini

# Optional (custom endpoint)
OPENAI_BASE_URL=https://api.openai.com/v1

# Business rules
PRICE_TOLERANCE_PCT=0.01
QTY_TOLERANCE_PCT=0.02

# Logging
LOG_LEVEL=INFO
```

### 3 — Start the stack

```bash
docker compose up -d
```

This brings up two services:

| Service | Port | Purpose |
|---|---|---|
| `redis` | 6379 | State store, audit trail, PO/GRN cache |
| `api` | 8000 | FastAPI — unified ingestion, approvals, search, health |

---

## Running

### API (FastAPI)

```bash
# Local
uvicorn orchestrate.api:app --reload --port 8000

# or via docker compose (already running on :8000)
```

Interactive docs at `http://localhost:8000/docs`.

#### Unified ingestion

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/ingest` | Ingest invoice/po/grn in json/text/image/pdf format |

**Request:**
```json
{
  "doc_type": "invoice",
  "format": "json",
  "data": { ... },
  "po_number": "PO-123"
}
```

**Response (invoice):**
```json
{
  "status": "accepted",
  "message": "Invoice INV-456 accepted for processing",
  "exception_id": "EXC-001"
}
```
*Returns 202 Accepted. Pipeline runs in background.*

**Response (po/grn):**
```json
{
  "status": "stored",
  "message": "PO PO-123 received and cached"
}
```
*Returns 200 OK.*

#### Exception management

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/tools/approve/{exception_id}` | Manually approve an escalated exception |
| `POST` | `/tools/reject/{exception_id}` | Manually reject an escalated exception |
| `POST` | `/exceptions/list` | Search & filter exceptions (supplier, invoice, PO, status, variance range) |

#### Health

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |

### Run the LangGraph pipeline programmatically

```python
from agent.langgraph_agent import run_pipeline
from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from config.settings import get_settings
from state.redis_backend import RedisStateStore

cfg = get_settings()
r = get_redis_connection(cfg.redis_url)
store = RedisStateStore(r)
audit = AuditLogger(RedisStreamsClient(r, "ap:audit:events"))

resolution = run_pipeline(
    exception_id="EXC-001",
    store=store,
    audit=audit,
    config=cfg,
)
print(resolution.final_state, resolution.memo.summary)
```

### Tests

```bash
pytest tests/ -v
```

All tests pass without external dependencies (uses `fakeredis`).

---

## Document Ingestion

The new `ingestion/normalizer.py` unified normalizer handles:

- **JSON**: Fast path — validate directly against Pydantic model. On ValidationError, fall back to LLM.
- **Text**: Pass to LLM with extraction prompt, parse JSON response, validate.
- **Image/PDF**: Convert to base64, send to vision-capable LLM with extraction prompt, validate.

**Supported models:** `invoice`, `po`, `grn`  
**Supported formats:** `json`, `text`, `image`, `pdf`

Example:

```python
from ingestion.normalizer import normalize_document

# JSON (fast path, no LLM call if valid)
invoice = normalize_document("invoice", "json", {"invoice_number": "INV-123", ...})

# Image with LLM extraction
invoice = normalize_document("invoice", "image", image_bytes)

# PDF with LLM extraction
po = normalize_document("po", "pdf", pdf_bytes)
```

---

## Exception Lifecycle

Each exception follows a strict state machine:

```
RECEIVED → TRIAGED → PENDING_APPROVAL → APPROVED
                  ↘                    ↗
                   ───ESCALATED───
                                  ↘ REJECTED
                    (RESOLVED short-circuits from TRIAGED on auto-approval)
```

State transitions are enforced by [state/machine.py](state/machine.py) and persisted atomically to Redis. Every transition is written to the append-only Redis Streams audit trail.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `OPENAI_API_KEY` | — | **Required.** OpenAI-compatible API key (vision-capable) |
| `OPENAI_BASE_URL` | — | Custom endpoint (leave blank for standard OpenAI) |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model name |
| `OPENAI_TIMEOUT_SECS` | `30` | Timeout for LLM calls |
| `REDIS_TIMEOUT_SECS` | `5` | Timeout for Redis operations |
| `PRICE_TOLERANCE_PCT` | `0.01` | Gate 2 auto-approve threshold (1%) |
| `QTY_TOLERANCE_PCT` | `0.02` | Classifier quantity variance threshold (2%) |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## What This System Is (and Isn't)

**Is:**
- A lightweight, in-process agent (no external Orchestrate service)
- An async pipeline via FastAPI BackgroundTasks (simpler than Celery)
- A full audit trail of every decision and transition
- A testable system (7+ tests covering unit and integration)
- Document-format agnostic (JSON, text, image, PDF via LLM)

**Is not:**
- A multi-tenant SaaS
- A learning system (human approvals don't feed back into rules)
- A replacement for SAP — requires pre-ingested documents

---

## Development

### Running tests locally

```bash
pytest tests/ -v
```

### Code structure

- **Pure functions**: `agent/` modules (classifier, rules, history, comms, memo)
- **I/O boundaries**: `orchestrate/api.py`, `state/redis_backend.py`, `ingestion/normalizer.py`
- **Models**: `models/` — Pydantic dataclasses (invoice, PO, GRN, exception, resolution)
- **State machine**: `state/machine.py` — enforces valid exception state transitions

### Adding a new gate

1. Add a gate function to `agent/rules_engine.py` (takes `InvoiceException`, returns `RulesDecision | None`)
2. Add a corresponding node and routing logic to `agent/langgraph_agent.py`
3. Wire the new node into the graph edges
4. Write a test in `tests/test_rules_engine.py`

---

## License

Internal use only (Meridian Corp).

---

**Questions?** See the project repository or contact the AP team.
