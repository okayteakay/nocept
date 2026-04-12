# Nocept — Autonomous Invoice Exception Resolution Agent

An AI-powered agent that autonomously triages and resolves invoice-to-PO mismatches in enterprise Accounts Payable workflows. Built for Meridian Corp's AP team, it handles price variances, quantity discrepancies, product substitutions, missing goods receipts, and duplicate invoices — approving or escalating each case through a deterministic six-step pipeline without human intervention where possible.

---

## How It Works

Every incoming invoice is run through six gates in sequence. The first gate that fires determines the outcome:

| Step | Gate | Fires when | Outcome |
|------|------|------------|---------|
| 1 | **Classify** | Always | Detects exception type and variance amount |
| 2 | **Tolerance** | Invoice-to-PO variance ≤ 1% | `AUTO_APPROVE` |
| 3 | **Historical precedent** | A similar case was approved in the past for the same supplier | `AUTO_APPROVE` |
| 4 | **Communications** | A linked email or call transcript confirms the exception (LLM-evaluated) | `AUTO_APPROVE` |
| 5 | **Web research** | Tavily finds a public source corroborating the exception | `AUTO_APPROVE` |
| 6 | **Escalate** | None of the above triggered | `ESCALATE_TO_HUMAN` |

Duplicate invoices are caught at Step 1 and immediately `AUTO_REJECT`ed before any gate runs.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Ingestion Layer                    │
│    JSON Dataset │ CSV Files │ Webhook (FastAPI)      │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│                  Agent Pipeline                      │
│                                                      │
│  Step 1: Classify          (type + variance)         │
│  Step 2: Tolerance gate    (≤ 1%)                    │
│  Step 3: History checker   (past approvals dataset)  │
│  Step 4: Comms checker     (OpenAI-compatible LLM)   │
│  Step 5: Web researcher    (Tavily Search API)       │
│  Step 6: Escalate / Resolve + Memo generation        │
│                                                      │
│  State & Audit  →  Redis + Redis Streams             │
└──────────────────────────┬──────────────────────────┘
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
        ┌─────────────┐      ┌──────────────────┐
        │  Dashboard  │      │  Orchestrate API  │
        │ (Streamlit) │      │   (FastAPI/REST)  │
        └─────────────┘      └──────────────────┘
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Agent pipeline | Python 3.10+ |
| Step 4 — comms LLM | OpenAI-compatible API (nanogpt) |
| Step 5 — web research | Tavily Search API |
| State & audit | Redis + Redis Streams |
| Orchestrate API | FastAPI |
| Dashboard | Streamlit |
| Package manager | uv |

---

## Project Structure

```
nocept/
├── agent/
│   ├── classifier.py             # Step 1 — exception type + variance detection
│   ├── rules_engine.py           # Six-gate decision engine
│   ├── history_checker.py        # Step 3 — historical precedent lookup
│   ├── comms_checker.py          # Step 4 — LLM-based communication analysis
│   ├── researcher.py             # Step 5 — Tavily web research
│   ├── context_retriever.py      # Supplier pattern context from Redis
│   ├── memo_generator.py         # Resolution memo writer
│   └── pipeline.py               # End-to-end pipeline runner
├── orchestrate/
│   └── api.py                    # FastAPI — six tool endpoints for watsonx Orchestrate
├── dataset/
│   ├── data/                     # JSON data files (invoices, POs, emails, etc.)
│   ├── generate_data.py                  # Synthetic Meridian Corp dataset
│   ├── generate_historical_approvals.py  # Historical approved exceptions
│   └── generate_real_company_data.py     # Real-company rows via Tavily
├── ingestion/                    # CSV, JSON, and webhook ingestors
├── knowledge/                    # Redis vector knowledge base (emails, transcripts)
├── models/                       # Pydantic data models
├── state/                        # Redis state machine + persistence
├── audit/                        # Redis Streams audit logger
├── config/                       # AppConfig (pydantic-settings, reads from .env)
├── dashboard/                    # Streamlit UI
└── tests/                        # Pytest suite
```

---

## Setup

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- Redis (local or Docker)
- Tavily API key
- OpenAI-compatible API key (nanogpt or standard OpenAI)

### Install

```bash
git clone https://github.com/okayteakay/nocept.git
cd nocept
uv sync
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
REDIS_URL=redis://localhost:6379/0
TAVILY_API_KEY=your-tavily-key

# OpenAI-compatible — used by comms checker (Step 4)
OPENAI_API_KEY=your-nanogpt-key
OPENAI_BASE_URL=https://nano-gpt.com/api/v1   # leave blank for standard OpenAI
OPENAI_MODEL=gpt-4o-mini

# Business rules
PRICE_TOLERANCE_PCT=0.01   # 1% auto-approve threshold (Step 2)
QTY_TOLERANCE_PCT=0.02     # classifier quantity variance threshold
```

### Start Redis

```bash
docker compose up -d redis
```

---

## Running

### Orchestrate API

Exposes six REST endpoints that map to the six pipeline steps. Import `/openapi.json` into IBM watsonx Orchestrate to register them as tools.

```bash
uvicorn orchestrate.api:app --reload --port 8001
```

```
POST /tools/intake               →  Step 1: classify and enqueue
GET  /tools/tolerance/{id}       →  Step 2: tolerance gate
GET  /tools/history/{id}         →  Step 3: historical precedent
GET  /tools/communications/{id}  →  Step 4: LLM comms analysis
GET  /tools/research/{id}        →  Step 5: Tavily web research
POST /tools/resolve/{id}         →  Step 6: finalize + generate memo
```

### Dashboard

```bash
streamlit run dashboard/app.py
```

### Run the pipeline programmatically

```python
from ingestion.json_ingestor import load_dataset
from agent.pipeline import run_pipeline
from config.settings import get_settings
from clients.redis_client import get_redis_connection
from clients.tavily_client import TavilyClient
from state.redis_backend import RedisStateStore
from audit.audit_logger import AuditLogger, RedisStreamsClient

config = get_settings()
r = get_redis_connection(config.redis_url)
dataset = load_dataset()

for invoice, po, gr, exc_record in dataset.exception_triples():
    result = run_pipeline(
        invoice, po, gr,
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

### Regenerate dataset

```bash
# Full synthetic Meridian Corp dataset
uv run python dataset/generate_data.py

# Historical approved exceptions (55 random + 12 targeted gap-fillers)
uv run python dataset/generate_historical_approvals.py

# Real-company rows for Step 5 web research validation (requires TAVILY_API_KEY)
uv run python dataset/generate_real_company_data.py
```

---

## Dataset

The `dataset/data/` directory contains seven JSON files representing Meridian Corp's AP data:

| File | Records | Description |
|------|---------|-------------|
| `invoices.json` | ~213 | Invoices with line items (SKU, qty, unit price, totals) |
| `purchase_orders.json` | ~213 | Matching POs |
| `goods_receipts.json` | ~203 | GR records confirming delivery |
| `exception_records.json` | 83 | Pre-classified exceptions with linked comms |
| `historical_approved_exceptions.json` | 67 | Past approved cases used by Step 3 |
| `emails.json` | ~104 | Supplier/buyer email threads |
| `phone_transcripts.json` | ~42 | Call transcripts between suppliers and buyers |
| `suppliers.json` | 54 | 12 synthetic + 42 real companies |

Exceptions span five types: `price_variance`, `quantity_variance`, `informal_modification`, `missing_goods_receipt`, `duplicate_invoice`. Real-company rows (FedEx, Nucor, Eastman Chemical, 3M, etc.) are included specifically for Step 5 web research validation — Tavily can find public pricing announcements and product discontinuation notices for these companies.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `TAVILY_API_KEY` | — | Tavily Search API key (Step 5) |
| `OPENAI_API_KEY` | — | OpenAI-compatible key (Step 4 LLM) |
| `OPENAI_BASE_URL` | — | Custom endpoint e.g. nanogpt (optional) |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for comms analysis |
| `PRICE_TOLERANCE_PCT` | `0.01` | Step 2 auto-approve threshold (1%) |
| `QTY_TOLERANCE_PCT` | `0.02` | Quantity variance classifier threshold |
| `LOG_LEVEL` | `INFO` | Python logging level |
