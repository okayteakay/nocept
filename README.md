# Nocept — Autonomous Invoice Exception Resolution

AI agent that autonomously resolves invoice-to-PO mismatches for Meridian Corp's AP team. Handles price variances, quantity discrepancies, product substitutions, missing goods receipts, and duplicate invoices through a six-step pipeline.

## Pipeline

| Step | Gate | Outcome |
|------|------|---------|
| 1 | Classify exception type + variance | — |
| 2 | Invoice-to-PO variance ≤ 1% | `AUTO_APPROVE` |
| 3 | Similar case approved historically for same supplier | `AUTO_APPROVE` |
| 4 | Linked email or call transcript confirms it (LLM) | `AUTO_APPROVE` |
| 5 | Tavily finds a public source corroborating it | `AUTO_APPROVE` |
| 6 | Nothing fired | `ESCALATE_TO_HUMAN` |

Duplicates are caught at Step 1 and immediately `AUTO_REJECT`ed.

## Stack

- **Agent logic** — Python 3.10+
- **Step 4 LLM** — OpenAI-compatible API (nanogpt)
- **Step 5 research** — Tavily Search API
- **State & audit** — Redis + Redis Streams
- **Orchestrate API** — FastAPI
- **Dashboard** — Streamlit
- **Packages** — uv

## Setup

```bash
git clone https://github.com/okayteakay/nocept.git
cd nocept
uv sync
cp .env.example .env  # fill in keys
docker compose up -d redis
```

`.env` keys needed:

```env
REDIS_URL=redis://localhost:6379/0
TAVILY_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=    # nanogpt endpoint, blank for standard OpenAI
OPENAI_MODEL=gpt-4o-mini
PRICE_TOLERANCE_PCT=0.01
QTY_TOLERANCE_PCT=0.02
```

## Running

```bash
# Orchestrate API (six tool endpoints)
uvicorn orchestrate.api:app --reload --port 8001

# Dashboard
streamlit run dashboard/app.py

# Tests
uv run pytest tests/ -v

# Regenerate dataset
uv run python dataset/generate_data.py
uv run python dataset/generate_historical_approvals.py
uv run python dataset/generate_real_company_data.py  # needs TAVILY_API_KEY
```

## Structure

```
agent/          classifier, rules_engine, history_checker, comms_checker,
                researcher, context_retriever, memo_generator, pipeline
orchestrate/    FastAPI tool endpoints
dataset/        JSON data files + generation scripts
ingestion/      CSV, JSON, webhook ingestors
knowledge/      Redis vector KB (emails, transcripts)
models/         Pydantic models
state/          Redis state machine
audit/          Redis Streams logger
config/         AppConfig (reads from .env)
dashboard/      Streamlit UI
tests/          Pytest suite
```
