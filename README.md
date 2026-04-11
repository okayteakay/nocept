# Project Nocept: Autonomous Invoice Exception Resolution Agent

An AI-powered agent that autonomously triages, researches, and resolves invoice-to-PO mismatches in enterprise Accounts Payable workflows — including discrepancies caused by undocumented offline order modifications.

## Architecture

```
                        ┌─────────────────────────────────────────────────────┐
                        │               Ingestion Layer                        │
                        │  CSV Files │ API Webhook (FastAPI) │ ERP Simulator  │
                        └──────────────────────┬──────────────────────────────┘
                                               │
                                               ▼
                        ┌─────────────────────────────────────────────────────┐
                        │               Agent Pipeline                         │
                        │                                                      │
                        │  b. Classify  →  c. Context  →  d. Research         │
                        │      │              (Redis)       (Tavily)           │
                        │      ▼                                               │
                        │  e. Rules Engine  →  f. Memo Generator              │
                        │      │                                               │
                        │      ▼                                               │
                        │  g. Persist (Redis) + Audit (Redis Streams)         │
                        └──────────────────────┬──────────────────────────────┘
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                           ┌──────────────┐    ┌─────────────────────┐
                           │   Dashboard  │    │  Spend Variance      │
                           │  (Streamlit) │    │  Report (aggregated) │
                           └──────────────┘    └─────────────────────┘
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Workflow Orchestration | IBM watsonx Orchestrate |
| External Research | Tavily Search API |
| State & Audit | Redis + Redis Streams |
| Agent Logic | Python 3.11+ |
| Dashboard | Streamlit |
| Webhook Receiver | FastAPI |

## Setup

### Prerequisites
- Python 3.11+
- Redis (local or remote)
- Tavily API key
- IBM watsonx credentials

### Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### Start Redis (Docker)
```bash
docker run -d -p 6379:6379 redis:7
```

## Running

### Full pipeline (single exception)
```python
from config.settings import get_settings
from clients.redis_client import get_redis_connection
from clients.tavily_client import TavilyClient
from state.redis_backend import RedisStateStore
from audit.audit_logger import AuditLogger, RedisStreamsClient
from agent.pipeline import run_pipeline
from ingestion.erp_simulator import generate_informal_modification_exception

config = get_settings()
r = get_redis_connection(config.redis_url)
store = RedisStateStore(r)
tavily = TavilyClient(config.tavily_api_key)
streams = RedisStreamsClient(r, "ap:audit:events")
audit = AuditLogger(streams)

invoice, po, grn = generate_informal_modification_exception("SUPP-001")
result = run_pipeline(invoice, po, grn, store, tavily, audit, config)
print(result.resolution.memo.summary)
```

### Webhook server
```bash
uvicorn ingestion.webhook_handler:app --reload --port 8000
```

### Streamlit dashboard
```bash
streamlit run dashboard/app.py
```

### Run tests
```bash
pytest tests/ -v
```

## Project Structure

```
receiptfinder/
├── config/          # Environment config (AppConfig)
├── models/          # Pydantic data models (Invoice, PO, GRN, Exception, Resolution)
├── clients/         # External API clients (Redis, Tavily)
├── state/           # State machine + Redis persistence
├── agent/           # Core pipeline steps (classify → research → resolve → memo)
├── ingestion/       # Data entry points (CSV, webhook, ERP simulator)
├── audit/           # Redis Streams audit logger
├── reports/         # Spend variance aggregation
├── dashboard/       # Streamlit UI
├── tests/           # Test suite
└── data/            # Drop CSV data files here (see data/README.md)
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `TAVILY_API_KEY` | — | Tavily Search API key |
| `WATSONX_API_KEY` | — | IBM watsonx API key |
| `WATSONX_URL` | `https://us-south.ml.cloud.ibm.com` | watsonx endpoint |
| `WATSONX_PROJECT_ID` | — | watsonx project ID |
| `PRICE_TOLERANCE_PCT` | `0.05` | Auto-approve price variance threshold (5%) |
| `QTY_TOLERANCE_PCT` | `0.02` | Auto-approve quantity variance threshold (2%) |
| `AUTO_RESOLVE_MAX_VARIANCE_USD` | `500` | Max USD variance for auto-resolution |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Data Format

See [data/README.md](data/README.md) for CSV schemas expected by the ingestion layer.
