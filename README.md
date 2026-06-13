# Nocept — Autonomous Invoice Exception Resolution Agent

> An AI-powered, self-contained AP agent that ingests invoice events from SAP S/4HANA, runs each one through a deterministic six-gate decision pipeline, and either auto-resolves it or escalates a fully-evidenced case to a human reviewer. Built for Meridian Corp's AP team.

Nocept is **no longer an IBM watsonx Orchestrate integration**. The decision pipeline now runs **in-process as a LangGraph state machine**, driven by a **Celery worker** that consumes events from an HMAC-signed SAP webhook receiver. The REST surface is plain **FastAPI**; the dashboard is **Streamlit**; everything is a six-service **docker-compose** stack.

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
                                              →  Slack/email notification fired
                                              →  knowledge base updated
```

Each gate is a pure function in [agent/](agent/); the orchestrator that wires them together is [agent/langgraph_agent.py](agent/langgraph_agent.py). The pipeline runs in a Celery worker, not inline in the webhook request.

---

## Architecture

```
                        SAP S/4HANA
                             │  HMAC-SHA256 signed JSON
                             ▼
                  ┌─────────────────────────┐
                  │  webhook  (FastAPI :8002) │   ingestion/webhook_handler.py
                  │  /webhook/po            │   ingestion/sap_mapper.py
                  │  /webhook/invoice       │   ingestion/ocr.py (PDF → text)
                  │  /webhook/grn           │
                  └────────────┬────────────┘
                               │  enqueue
                               ▼
                  ┌─────────────────────────┐
                  │  Redis Stack :6379      │   broker + result backend
                  │  DB 0 — state, audit, KB│   DB 1 — Celery queues
                  └────────────┬────────────┘
                               │  consume "ap_pipeline" queue
                               ▼
                  ┌─────────────────────────┐
                  │  worker  (Celery)       │   worker/tasks.py
                  │                         │   ↳ agent/langgraph_agent.py
                  │  Six-gate LangGraph DAG:│      ├─ classifier
                  │   classify → context →  │      ├─ rules_engine
                  │   gate_tolerance →      │      ├─ history_checker
                  │   gate_history →        │      ├─ comms_checker (LLM)
                  │   gate_comms → research │      ├─ researcher (Tavily)
                  │   → generate_memo →     │      ├─ context_retriever
                  │   persist               │      └─ memo_generator
                  └────────────┬────────────┘
                               │  write
                               ▼
   ┌──────────────┐  ┌─────────────────────┐  ┌────────────────────────┐
   │  Redis Stack │  │  Audit Trail         │  │  Knowledge Base        │
   │              │  │                      │  │                        │
   │  state store │  │  Redis Streams       │  │  Vector search         │
   │  queue index │  │  (append-only,       │  │  (emails, transcripts, │
   │  supplier    │  │   SOX-compliant)     │  │   resolution history)  │
   │  index       │  │                      │  │                        │
   │  state index │  │                      │  │                        │
   └──────┬───────┘  └─────────────────────┘  └────────────┬───────────┘
          │                                                │
          │          ┌─────────────────────┐               │
          └─────────►│  api  (FastAPI :8000)│◄──────────────┘
                     │  orchestrate/api.py │
                     │                     │
                     │  Auth (/auth/*)     │
                     │  Tools (/tools/*)   │
                     │  Dashboard          │
                     │   (/exceptions/*,   │
                     │    /tools/approve,  │
                     │    /tools/reject)   │
                     │  Knowledge Base     │
                     │   (/kb/search/*)    │
                     │  Analytics          │
                     │   (/analytics/*)    │
                     │  Rules (/rules)     │
                     │  OpenAPI / Swagger  │
                     └──┬──────────────┬───┘
                        │              │
                        ▼              ▼
              ┌──────────────┐  ┌────────────────┐
              │  dashboard   │  │  notifications  │
              │  (Streamlit) │  │  Slack + SMTP   │
              │  :8502       │  │  (notifier.py)  │
              └──────────────┘  └────────────────┘
                        │
                        ▼
              ┌──────────────┐
              │  flower      │  Celery task monitor (:5555)
              │  (Celery UI) │
              └──────────────┘
```

There are two ways to drive the six-gate pipeline:

1. **Webhook → Celery (production path).** SAP fires a signed webhook, the worker invokes `agent.langgraph_agent.run_pipeline`, and the resolution is persisted, audited, and notified automatically.
2. **REST stepping-stone path.** A client (e.g. an interactive watsonx Orchestrate demo, or a hand-driven test) calls the six `/tools/*` endpoints in order on the same `agent/*` business logic. Both paths share the same code and the same audit trail.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Agent framework | **LangGraph** (in-process state machine) |
| Async workers | **Celery** + Redis broker |
| Web framework | FastAPI + Uvicorn |
| Dashboard | Streamlit |
| Auth | JWT/OAuth2 password flow (`python-jose` + `passlib`) |
| State & queue | Redis Stack (with Search/HNSW module) |
| Audit trail | Redis Streams (append-only, SOX-compliant) |
| Vector search | Redis Stack + `sentence-transformers` (`all-MiniLM-L6-v2`, 384-d) |
| Step 4 — comms LLM | OpenAI-compatible API (nanogpt by default, or standard OpenAI) |
| Step 5 — web research | Tavily Search API |
| OCR (PDF invoices) | Tesseract + poppler |
| Data validation | Pydantic v2 |
| Config | `pydantic-settings` (reads `.env`) |
| Notifications | Slack incoming webhooks + SMTP email |
| Package manager | `uv` |
| Container | Docker + Docker Compose (6 services) |

---

## Project Structure

```
nocept/
│
├── agent/                          # Core decision pipeline
│   ├── langgraph_agent.py          # In-process LangGraph orchestrator (production path)
│   ├── pipeline.py                 # Sequential orchestrator (legacy / single-process demo)
│   ├── classifier.py               # Step 1 — three-way match, variance detection
│   ├── rules_engine.py             # All six decision gates + RulesDecision model
│   ├── history_checker.py          # Step 3 — historical precedent lookup
│   ├── comms_checker.py            # Step 4 — LLM + fallback keyword analysis
│   ├── researcher.py               # Step 5 — Tavily queries + evidence scoring
│   ├── context_retriever.py        # Supplier pattern context from Redis
│   └── memo_generator.py           # Resolution memo assembly
│
├── orchestrate/
│   ├── api.py                      # FastAPI: auth, tools, dashboard, KB, analytics, rules
│   └── agent_prompt.md             # System prompt (kept for reference)
│
├── worker/                         # Celery async pipeline
│   ├── celery_app.py               # Celery configuration
│   └── tasks.py                    # process_exception task with retry + backoff
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
├── auth/
│   └── jwt_auth.py                 # OAuth2 password flow, JWT issuance + verification
│
├── rules/                          # User-configurable approval rules
│   ├── models.py                   # ApprovalRule, RuleType, RuleAction, RuleEvaluationResult
│   └── engine.py                   # Priority-based rule evaluator (8 rule types)
│
├── notifications/
│   ├── models.py                   # Notification records
│   ├── notifier.py                 # High-level notifier (Slack + SMTP)
│   └── sender.py                   # Low-level Slack + email senders
│
├── clients/
│   ├── redis_client.py             # Connection factory + RedisStreamsClient
│   └── tavily_client.py            # TavilyClient — search, supplier context, price changes
│
├── ingestion/                      # Inbound data + ERP integration
│   ├── webhook_handler.py          # FastAPI: signed PO/Invoice/GRN webhooks
│   ├── sap_mapper.py               # SAP IDoc/BAPI → internal models
│   ├── json_ingestor.py            # DatasetBundle — loads and cross-links all JSON data
│   ├── erp_simulator.py            # Generates synthetic invoice/PO/GRN tuples
│   └── ocr.py                      # Tesseract OCR for PDF invoices
│
├── analytics/
│   └── calculator.py               # KPIs, supplier scorecards, trends
│
├── reports/
│   └── spend_variance.py           # Spend variance report (per supplier / category / time)
│
├── config/
│   └── settings.py                 # AppConfig (pydantic-settings, .env-backed, cached)
│
├── dashboard/
│   └── app.py                      # Streamlit: queue, audit, detail view, approvals, analytics
│
├── dataset/
│   ├── data/                       # Seven JSON data files — Meridian Corp AP dataset
│   ├── generate_data.py            # Synthetic dataset generator
│   ├── generate_historical_approvals.py  # Historical approved exceptions generator
│   └── generate_real_company_data.py     # Real-company rows for Tavily validation
│
├── tests/                          # Pytest suite (33+ tests)
│   ├── conftest.py                 # Shared fixtures (fakeredis, models, mocked clients)
│   ├── test_approval_workflow.py   # 13 tests — state machine + human approval
│   ├── test_e2e_full_system.py     # 5 E2E tests
│   ├── test_load_concurrent.py     # 6 load tests (1000+ concurrent exceptions)
│   ├── test_sap_integration.py     # 9 SAP webhook tests
│   ├── test_classifier.py
│   ├── test_rules_engine.py
│   ├── test_history_checker.py
│   ├── test_context_retriever.py
│   ├── test_memo_generator.py
│   ├── test_researcher.py          # Includes live Tavily integration tests
│   ├── test_comms_checker.py
│   ├── test_state_machine.py
│   ├── test_pipeline.py
│   └── test_step7_exception_queue.py
│
├── run_demo.py                     # End-to-end demo script with KB search output
├── docker-compose.yml              # 6 services: redis, api, webhook, worker, dashboard, flower
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Setup

### Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) — fast Python package manager
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose — for the full stack
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

Edit `.env` (only the keys you need):

```env
# --- Required ---
REDIS_URL=redis://localhost:6379/0
TAVILY_API_KEY=your-tavily-key
OPENAI_API_KEY=your-openai-or-nanogpt-key

# --- Optional (LLM provider override) ---
OPENAI_BASE_URL=https://nano-gpt.com/api/v1
OPENAI_MODEL=gpt-4o-mini

# --- Business rules ---
PRICE_TOLERANCE_PCT=0.01    # Step 2: auto-approve if variance ≤ 1%
QTY_TOLERANCE_PCT=0.02      # Classifier: flag quantity delta > 2%

# --- Celery (defaults shown) ---
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# --- Notifications (optional but recommended) ---
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
SLACK_ESCALATION_CHANNEL=#ap-escalations
SMTP_HOST=smtp.your-company.com
SMTP_PORT=587
SMTP_USER=ap-bot@your-company.com
SMTP_PASSWORD=...
NOTIFICATION_EMAIL_TO=ap-team@your-company.com

# --- SAP webhook security ---
SAP_WEBHOOK_SECRET=change-me-to-a-strong-random-value

# --- JWT auth (required for dashboard/API) ---
JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# --- Embeddings / KB ---
EMBEDDING_MODEL=all-MiniLM-L6-v2
VECTOR_DIMENSIONS=384
VECTOR_INDEX_PREFIX=kb:

LOG_LEVEL=INFO
```

### 3 — Start the full stack

```bash
docker compose up -d
```

This brings up all six services:

| Service | Port | Purpose |
|---|---|---|
| `redis` | 6379 + 8001 (Insight) | State store, audit, KB, Celery broker |
| `api` | 8000 | FastAPI — auth, tools, dashboard endpoints, Swagger |
| `webhook` | 8002 | HMAC-signed SAP event receiver (PO / Invoice / GRN) |
| `worker` | — | Celery worker running the LangGraph pipeline (4 concurrency) |
| `dashboard` | 8502 | Streamlit UI (login with `admin` / `admin123`) |
| `flower` | 5555 | Celery task monitor |

---

## Running

### Dashboard (Streamlit)

```bash
# Local (uv)
streamlit run dashboard/app.py
# or via docker compose (already running on :8502)
```

Provides a live view of:
- The exception queue (filterable by type, status, supplier, variance range, invoice/PO)
- Each exception's PO-vs-Invoice comparison, classification rationale, and research evidence
- A **Human Approval** section for escalated exceptions (Approve / Reject + notes, audit-logged)
- Live KPIs: total processed, auto-resolution rate, avg resolution time, total variance, undocumented-modification variance
- A spend-variance report with per-supplier / per-category time series and CSV export

Login with the demo user `admin` / `admin123` (defined in [auth/jwt_auth.py](auth/jwt_auth.py)).

### Orchestrate API (FastAPI)

```bash
# Local
uvicorn orchestrate.api:app --reload --port 8000
# or via docker compose (already running on :8000)
```

Interactive docs at `http://localhost:8000/docs`.

#### Auth

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/auth/token` | Issue access_token (30 min) + refresh_token (7 days) |
| `POST` | `/auth/refresh` | Refresh an access token |
| `GET`  | `/auth/me` | Current user info |

#### Six-step tools (REST stepping-stone path)

Each tool is the HTTP wrapper around the same business logic that the LangGraph agent runs in-process. You can call them in sequence to reproduce the pipeline from an external orchestrator.

| Method | Endpoint | Step |
|---|---|---|
| `POST` | `/tools/intake` | 1 — Classify, detect exception types, persist |
| `GET`  | `/tools/tolerance/{id}` | 2 — Auto-approve if variance ≤ 1% |
| `GET`  | `/tools/history/{id}` | 3 — Auto-approve on historical precedent |
| `GET`  | `/tools/communications/{id}` | 4 — Auto-approve on comms confirmation |
| `POST` | `/tools/research/{id}` | 5 — Auto-approve on web research |
| `POST` | `/tools/resolve/{id}` | 6 — Finalize, generate memo, write resolution |

#### Human approval endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/tools/approve/{id}` | Manually approve an escalated exception (with notes) |
| `POST` | `/tools/reject/{id}` | Manually reject an escalated exception (with reason) |

#### Dashboard / search / list

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/exceptions/list` | Search & filter the exception queue (supplier, invoice, PO, status, variance range) |

#### Knowledge base

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/kb/search/emails` | Semantic search over indexed emails |
| `POST` | `/kb/search/transcripts` | Semantic search over indexed transcripts |
| `GET`  | `/kb/history/{supplier_id}` | Supplier resolution history summary |

#### Analytics

| Method | Endpoint | Purpose |
|---|---|---|
| `GET`  | `/analytics/summary` | KPIs, supplier scorecard, daily trends |

#### Rules engine

| Method | Endpoint | Purpose |
|---|---|---|
| `GET`    | `/rules` | List configured approval rules |
| `POST`   | `/rules` | Create a new rule |
| `PUT`    | `/rules/{rule_id}` | Update a rule |
| `DELETE` | `/rules/{rule_id}` | Delete a rule |

Eight rule types are supported: amount thresholds (>, <), supplier whitelist/blacklist, exception-type matching, days-overdue, supplier approval rate, and duplicate submission detection. Rules are evaluated in priority order; the first match wins.

#### Webhooks (SAP S/4HANA)

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/webhook/po` | Store PO in Redis (`po:<po_number>`) for later matching |
| `POST` | `/webhook/invoice` | Create exception, enqueue Celery task (202 Accepted) |
| `POST` | `/webhook/grn` | Store GRN, re-trigger any `MISSING_GOODS_RECEIPT` exceptions |
| `GET`  | `/health` | Liveness probe |

All webhooks verify an `X-SAP-Signature` header (HMAC-SHA256 of the body using `SAP_WEBHOOK_SECRET`). Idempotency is by `po_number` / `invoice_number`.

### End-to-end demo

```bash
uv run python run_demo.py
```

Generates a synthetic informal-modification exception, runs the full pipeline, prints the resolution memo, and demonstrates knowledge-base search. Or use the **Demo Trigger** section in the Streamlit dashboard to run scenarios one at a time.

### Run the LangGraph pipeline programmatically

```python
from agent.langgraph_agent import run_pipeline
from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import get_settings
from state.redis_backend import RedisStateStore

cfg = get_settings()
r = get_redis_connection(cfg.redis_url)
store = RedisStateStore(r)
audit = AuditLogger(RedisStreamsClient(r, "ap:audit:events"))
tavily = TavilyClient(cfg.tavily_api_key)

resolution = run_pipeline(
    exception_id="<id from /webhook/invoice response>",
    store=store,
    audit=audit,
    config=cfg,
    tavily=tavily,
)
print(resolution.final_state, resolution.memo.summary)
```

### Tests

```bash
uv run pytest tests/ -v
```

Live Tavily tests in `TestTavilyLive` are auto-skipped unless `TAVILY_API_KEY` is set:

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
RECEIVED → TRIAGED → RESEARCHING → PENDING_APPROVAL → APPROVED
                                 ↘                   ↗
                                  ─────ESCALATED─────
                                                   ↘ REJECTED
                                       (RESOLVED short-circuits from TRIAGED on auto-approval)
```

State transitions are enforced by [state/machine.py](state/machine.py) and persisted atomically to Redis. Every transition is written to the append-only Redis Streams audit trail, and the API uses BFS over the transition graph to walk to a target state without ever making an illegal jump.

---

## Knowledge Base

On startup, the Orchestrate API seeds a Redis Stack knowledge base with all historical resolutions, emails, and transcripts from the dataset. This powers:

- **Semantic email search** (`POST /kb/search/emails`) — find emails by meaning, not just keywords, with optional date/PO/invoice filters
- **Semantic transcript search** (`POST /kb/search/transcripts`) — same for call transcripts
- **Supplier resolution history** (`GET /kb/history/{supplier_id}`) — aggregate stats: total exceptions, resolution rate, most common types, average variance
- **Step 3 historical precedent** — structured lookups over the `resolution_store` index when an exception is being triaged

Embeddings use `all-MiniLM-L6-v2` (384 dimensions, ~90 MB, downloaded from HuggingFace on first run and cached). Vectors are L2-normalized for cosine similarity over Redis HNSW indexes.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis Stack connection string (state, audit, KB) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Celery result backend |
| `TAVILY_API_KEY` | — | Tavily Search API key (Step 5) |
| `OPENAI_API_KEY` | — | OpenAI-compatible key (Step 4 LLM) |
| `OPENAI_BASE_URL` | — | Custom endpoint e.g. nanogpt — leave blank for standard OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name for communications analysis |
| `PRICE_TOLERANCE_PCT` | `0.01` | Step 2 auto-approve threshold (1%) |
| `QTY_TOLERANCE_PCT` | `0.02` | Classifier quantity variance threshold (2%) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model for KB vector search |
| `VECTOR_DIMENSIONS` | `384` | Output dimensions of the embedding model |
| `VECTOR_INDEX_PREFIX` | `kb:` | Redis key namespace for all knowledge-base entries |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook for escalation alerts |
| `SLACK_ESCALATION_CHANNEL` | `#ap-escalations` | Slack channel label |
| `SMTP_HOST` | `localhost` | SMTP server for email notifications |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP credentials |
| `SMTP_FROM_EMAIL` | `noreply@meridian-ap.local` | From address |
| `NOTIFICATION_EMAIL_TO` | — | Comma-separated escalation recipients |
| `SAP_WEBHOOK_SECRET` | — | Shared secret for SAP webhook HMAC-SHA256 |
| `JWT_SECRET_KEY` | `dev-secret-key-change-in-production` | **Must override in production** |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `OPENAI_TIMEOUT_SECS` | `30` | Timeout for OpenAI calls |
| `TAVILY_TIMEOUT_SECS` | `30` | Timeout for Tavily calls |
| `REDIS_TIMEOUT_SECS` | `5` | Timeout for Redis operations |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## What This System Is (and Isn't)

**Is:**
- A self-contained, in-process agent (no external Orchestrate service)
- An async, idempotent pipeline that scales horizontally via Celery
- A full audit trail of every decision and transition
- A demonstrably testable system (33+ tests covering unit, E2E, load, and SAP integration)

**Is not (yet):**
- A multi-tenant SaaS — currently single-tenant (`org_id` field exists but isn't enforced across keys)
- A continuous-learning model — human approvals are stored but don't yet feed back into rules or prompts automatically
- A production auth system — JWT is implemented but user store is a flat JSON file
- A durable rules store — `/rules` currently uses an in-memory list (process-local)

See [WEEK6_DEPLOYMENT_PLAN.md](WEEK6_DEPLOYMENT_PLAN.md) and [FINAL_PRODUCTION_READINESS_REPORT.md](FINAL_PRODUCTION_READINESS_REPORT.md) for the production-hardening roadmap and known limitations.
