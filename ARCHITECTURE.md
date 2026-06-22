# Architecture Documentation: Invoice Exception Resolution System

**Version:** 5.0 (Debloated)  
**Date:** June 2026  
**System Name:** Nocept — Autonomous Invoice Exception Resolution Agent

---

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level System Diagram](#high-level-system-diagram)
3. [Component Architecture](#component-architecture)
4. [Data Flow](#data-flow)
5. [State Machine](#state-machine)
6. [Decision Pipeline](#decision-pipeline)
7. [Technology Stack](#technology-stack)
8. [Deployment Architecture](#deployment-architecture)

---

## System Overview

### Purpose

Nocept automatically triages and resolves invoice-to-PO mismatches in enterprise Accounts Payable workflows. It eliminates ~70% of routine manual exception reviews through intelligent decision gates, while escalating complex cases to human managers with evidence-based recommendations.

### Key Outcomes

- **60–80% Auto-Resolution Rate:** Invoices matching tolerance, history, or comms auto-approve without human involvement
- **Evidence-Based Escalations:** Remaining 20–40% escalated with supporting evidence
- **Immutable Audit Trail:** All decisions logged to Redis Streams for compliance
- **Minimal Dependencies:** No external orchestration, knowledge base, or research APIs — pure LLM + Redis

### Core Philosophy

1. **Conservative Auto-Approval:** System only auto-approves when evidence is strong (confidence 0.75+)
2. **Deterministic Gates:** Four sequential gates; first one that fires determines outcome
3. **Explainability:** Every decision backed by evidence (variance breakdown, linked comms)
4. **Human Partnership:** Managers make final decisions; system provides analysis

---

## High-Level System Diagram

```
                    Unified Ingest Endpoint
                    (invoice|po|grn + json|text|image|pdf)
                             │
                             ▼
                  ┌─────────────────────────┐
                  │  LLM Normalizer         │
                  │  (ingestion/normalizer) │
                  │  Fast path: JSON        │
                  │  Fallback: LLM vision   │
                  └────────────┬────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
         ▼ (PO/GRN)                  ▼ (Invoice)
      Cache in                   Create exception
      Redis 30d                  │
      TTL                        ▼
                        ┌─────────────────┐
                        │ LangGraph       │
                        │ Pipeline        │
                        │ (In-process)    │
                        │                 │
                        │ [1] Classify    │
                        │ [2] Tolerance   │
                        │ [3] History     │
                        │ [4] Comms       │
                        │ [Escalate if    │
                        │  no gate fired] │
                        │                 │
                        │ Generate memo   │
                        │ Persist result  │
                        └────────┬────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
            ┌──────────────┐         ┌────────────────┐
            │ Redis Stack  │         │ FastAPI Endpoints│
            │              │         │                │
            │ State store  │         │ /tools/approve │
            │ Audit trail  │         │ /exceptions/list│
            │ Cache        │         │ /health        │
            └──────────────┘         └────────────────┘
                                              ▲
                                              │
                                    Human reviewer / client
```

---

## Component Architecture

### **1. Ingestion Layer** (`ingestion/normalizer.py`)

Unified front door for all document formats and types.

**Responsibilities:**
- Accept invoice|po|grn in json|text|image|pdf format
- **Fast path (JSON):** Validate directly against Pydantic model. No LLM call if valid.
- **Fallback path (text/image/PDF):** Send to vision-capable LLM with structured extraction prompt
- Strip code fences and parse JSON response
- Validate result against model schema
- Return validated Pydantic instance or raise 422

**Key functions:**
- `normalize_document(doc_type, format, data)` → validated model instance
- `NormalizerClient.normalize()` — main entry point
- Vision LLM integration using OpenAI-compatible API

### **2. API Layer** (`orchestrate/api.py`)

RESTful interface for document ingestion and exception management.

**Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ingest` | Unified document ingestion (invoice, PO, GRN) |
| `POST` | `/tools/approve/{id}` | Manually approve escalated exception |
| `POST` | `/tools/reject/{id}` | Manually reject escalated exception |
| `POST` | `/exceptions/list` | Search/filter exceptions |
| `GET` | `/health` | Liveness probe |

**Ingestion logic:**
- **PO:** Normalize → cache in Redis as `po:<po_number>` with 30-day TTL → audit event
- **GRN:** Normalize → cache as `grn:<po_number>` → check for `MISSING_GOODS_RECEIPT` exceptions on same PO → re-trigger pipeline for matching exceptions
- **Invoice:** Normalize → look up cached PO (422 if missing) → create `InvoiceException` in RECEIVED state → enqueue background task → return 202 Accepted

### **3. Pipeline Layer** (`agent/langgraph_agent.py`)

In-process LangGraph state machine orchestrating the four decision gates.

**Architecture:**
- Replaces Celery worker — now runs via FastAPI `BackgroundTasks`
- Graph nodes: `classify` → `get_context` → `gate_tolerance` → `gate_history` → `gate_comms` → `generate_memo` → `persist`
- Each node is a pure function with injected dependencies (store, audit, config)
- Conditional edges route based on gate results
- No in-memory checkpointer (end-to-end latency <30s)

**Entry point:** `run_pipeline(exception_id, store, audit, config)` → `Resolution`

### **4. Decision Gates** (`agent/rules_engine.py`)

Pure functions implementing the four gate logic.

**Gate 1: Classify**
- Detect exception types (duplicate, price variance, qty variance, missing GRN, informal modification)
- Compute total variance USD and percentage
- Return: `ClassificationResult` or `RulesDecision(AUTO_REJECT)` if duplicate

**Gate 2: Tolerance**
- Check: invoice-to-PO variance ≤ 1%?
- Return: `RulesDecision(AUTO_APPROVE, confidence=1.0)` or None (continue)

**Gate 3: History**
- Look up historical approvals for this supplier with similar variance/type
- Return: `RulesDecision(AUTO_APPROVE, confidence=0.90)` or None

**Gate 4: Communications**
- Link exception to emails/transcripts by supplier/invoice
- LLM analysis: Does communication explain the variance?
- Return: `RulesDecision(AUTO_APPROVE, confidence=0.85)` or None

**Escalation (if no gate fires)**
- All four gates exhausted without decision → `RulesDecision(ESCALATE_TO_HUMAN, confidence varies)`
- Exception routed to human reviewer with supporting evidence

### **5. State Management** (`state/machine.py`, `state/redis_backend.py`)

Enforces valid exception state transitions and persists to Redis.

**State machine:**
```
RECEIVED → TRIAGED → PENDING_APPROVAL → APPROVED
                  ↘                    ↗
                   ───ESCALATED───
                                  ↘ REJECTED
```

**RedisStateStore:**
- `save(exception)` — atomic upsert to `exception:<id>`
- `load(exception_id)` — fetch from Redis
- `transition(exception_id, next_state)` — validate and update state
- `list_by_state(state)` — get all exception IDs in a given state
- `list_queue_ids()` — get RECEIVED exceptions (priority queue)

**Audit integration:**
- Every state transition logged to Redis Streams (`ap:audit:events`)
- Immutable append-only log for compliance

### **6. Audit Trail** (`audit/audit_logger.py`)

All decisions and transitions recorded to Redis Streams.

**Events logged:**
- `classification` — exception types detected
- `gate_fired` — which gate triggered approval/escalation
- `human_approval` — approver ID, notes, timestamp
- `human_rejection` — reviewer ID, reason, timestamp
- `state_transition` — from/to state
- `resolution` — final memo, confidence, action

---

## Data Flow

### **Document Ingest (PO Example)**

```
POST /ingest
{
  "doc_type": "po",
  "format": "json",
  "data": { "po_number": "PO-123", ... }
}
  │
  ├─→ ingestion.normalizer.normalize_document()
  │   ├─ format="json" → fast path
  │   └─ PurchaseOrder.model_validate(data)
  │
  ├─→ POST /ingest handler
  │   ├─ r.set("po:PO-123", json, ex=86400*30)
  │   └─ audit.log(AuditEvent(...))
  │
  └─→ 200 OK { "status": "stored", "message": "..." }
```

### **Invoice Ingest (Full Pipeline)**

```
POST /ingest
{
  "doc_type": "invoice",
  "format": "image",
  "data": base64_encoded_image,
  "po_number": "PO-123"
}
  │
  ├─→ ingestion.normalizer.normalize_document()
  │   ├─ format="image" → LLM vision path
  │   ├─ base64_decode() → PNG/JPEG bytes
  │   ├─ LLM extraction prompt
  │   └─ Invoice.model_validate(json_from_llm)
  │
  ├─→ POST /ingest handler
  │   ├─ po_json = r.get("po:PO-123")
  │   ├─ if not po_json: raise 422 (PO not found)
  │   ├─ grn_json = r.get("grn:PO-123")  # optional
  │   │
  │   ├─ exc = InvoiceException(
  │   │   invoice=parsed_invoice,
  │   │   purchase_order=parsed_po,
  │   │   grn=parsed_grn or None,
  │   │   state=RECEIVED
  │   │ )
  │   │
  │   ├─ store.save(exc)
  │   ├─ audit.log("invoice_received", ...)
  │   │
  │   ├─ bg_tasks.add_task(
  │   │   _run_pipeline_background,
  │   │   exc.exception_id,
  │   │   store,
  │   │   audit,
  │   │   cfg
  │   │ )
  │   │
  │   └─ 202 Accepted {
  │       "status": "accepted",
  │       "exception_id": "EXC-001"
  │     }
  │
  └─→ [In background]
      │
      ├─→ run_pipeline(exception_id, store, audit, cfg)
      │   │
      │   ├─ Load exception
      │   ├─ Build LangGraph (classifier → context → gates → memo → persist)
      │   ├─ Invoke graph with initial state
      │   │   │
      │   │   ├─[1] node_classify() → exception_types
      │   │   ├─[2] node_get_context() → supplier patterns
      │   │   ├─[3] node_gate_tolerance() → decision or continue
      │   │   ├─[4] node_gate_history() → decision or continue
      │   │   ├─[5] node_gate_comms() → decision or continue
      │   │   ├─[6] node_generate_memo() → memo
      │   │   ├─[7] node_persist() → save to Redis
      │   │   │
      │   │   └─→ Resolution(memo, final_state)
      │   │
      │   └─ Return Resolution
      │
      └─→ Resolution persisted to Redis & audit trail
          Exception state: RESOLVED or ESCALATED
          Audit events logged for every transition
```

---

## State Machine

```
          ┌──────────────┐
          │   RECEIVED   │
          │ (initial)    │
          └──────┬───────┘
                 │ classify()
                 ▼
          ┌──────────────┐
          │   TRIAGED    │
          └──────┬───────┘
                 │ gate_tolerance/history/comms
                 ├─→ Decision.auto_resolvable=True
                 │   └─→ RESOLVED (short-circuit)
                 │
                 └─→ All gates exhausted (no decision)
                     └─→ ESCALATED
                         │
                         ├─→ human_approve()
                         │   └─→ APPROVED
                         │
                         └─→ human_reject()
                             └─→ REJECTED
```

**Valid transitions:**
- RECEIVED → TRIAGED
- TRIAGED → RESOLVED (auto-approval)
- TRIAGED → ESCALATED (no gate fired)
- ESCALATED → APPROVED (human approval)
- ESCALATED → REJECTED (human rejection)

Enforced by `state.machine.VALID_TRANSITIONS` graph; API uses BFS to walk shortest path without illegal jumps.

---

## Decision Pipeline

### Gate Execution Flow

Each gate is a conditional: if condition fires, return `RulesDecision`; else return `None` and continue.

```python
# Gate 2: Tolerance
decision = gate_tolerance(exception, config)
if decision:
    audit.log("gate_fired", gate="tolerance", ...)
    return {"decision": decision}

# Gate 3: History
result = check_historical_approval(exception)
decision = None
if result.auto_approve:
    decision, _ = gate_history(exception)
    if decision:
        audit.log("gate_fired", gate="history", ...)
return {"decision": decision, "history_result": result}
```

### Memo Generation

Once a decision is made (or escalation is triggered), `node_generate_memo()` assembles:

1. **Evidence items** from supplier history (substitution patterns)
2. **Rule engine rationale** (why gate fired or escalated)
3. **Summary** — plain-English narrative
4. **Recommended action** — AUTO_APPROVE, AUTO_REJECT, ESCALATE_TO_HUMAN, etc.

### Persistence

`node_persist()` saves:
- Walk state machine from current → final (RESOLVED or ESCALATED)
- Create `Resolution` record with memo, final_state, timestamp
- Persist to Redis
- Log all transitions to audit trail

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Language** | Python 3.11+ | Type-safe, LLM-friendly |
| **Orchestration** | LangGraph | In-process state machine (replaces Celery) |
| **Async Execution** | FastAPI BackgroundTasks | Lightweight async (no separate worker) |
| **Web Framework** | FastAPI + Uvicorn | REST API, auto-docs, async |
| **State Store** | Redis (key-value) | Atomicity, FIFO queue, TTL cache |
| **Audit Log** | Redis Streams | Append-only, immutable, compliant |
| **Data Validation** | Pydantic v2 | Type hints, validation, JSON serialization |
| **Config** | pydantic-settings | Environment variable binding |
| **LLM** | OpenAI-compatible | Comms analysis + document normalization (vision-capable required) |
| **OCR** | pdf2image + Pillow | Convert PDF to images (no Tesseract) |
| **Container** | Docker + Compose | 2 services: redis + api |
| **Testing** | pytest + fakeredis | Unit + integration, no external deps |

---

## Deployment Architecture

### Docker Compose (Production)

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  api:
    build: .
    ports:
      - "8000:8000"
    depends_on:
      - redis
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
```

**Service Map:**
- `redis:6379` — State store, audit trail, cache
- `api:8000` — FastAPI (unified ingestion + approvals + search + health)

### Configuration

All runtime configuration via `.env`:
- `REDIS_URL` — Redis connection (required)
- `OPENAI_API_KEY` — Vision-capable LLM (required)
- `OPENAI_MODEL` — Model name (default: `gpt-4o-mini`)
- `OPENAI_TIMEOUT_SECS` — LLM timeout (default: 30)
- Business rules: `PRICE_TOLERANCE_PCT`, `QTY_TOLERANCE_PCT`

### Scaling Considerations

**Horizontal scaling:**
- Deploy multiple `api` instances behind a load balancer
- All share the same Redis instance (state store)
- BackgroundTasks run on same process as HTTP handler (no separate worker pool)

**Vertical scaling:**
- Increase Redis memory for larger exception queues
- Tune `OPENAI_TIMEOUT_SECS` for slower LLM endpoints

**Monitoring:**
- Track Redis memory usage (audit trail grows unbounded in theory)
- Monitor LLM API rate limits and latency
- Watch exception queue depth via `store.list_queue_ids()`

---

## Key Design Decisions

### Why No Celery?

Celery introduces operational complexity (separate worker process, broker management, result backend). BackgroundTasks are simpler, sufficient for <30s latency, and run within the same FastAPI process.

### Why Remove Knowledge Base?

The KB added complexity (sentence-transformers, Redis vector indexes, embedding latency) for limited value. Historical lookup via structured Redis queries is sufficient.

### Why Remove Web Research (Tavily)?

Web search added cost, latency, and complexity for edge cases. The comms gate handles most variance explanations. Escalation to humans handles the rest.

### Why Unified Normalizer?

Instead of separate SAP mapper + OCR + LLM, one normalizer with fast/fallback paths reduces code and makes document ingestion flexible (JSON, text, image, PDF).

---

## Security & Compliance

- **Audit Trail:** Immutable Redis Streams log (SOX-compliant)
- **State Transitions:** Validated by state machine (no illegal jumps)
- **No Auth:** Removed JWT auth (deploy behind API gateway in production)
- **No Encryption:** Redis communication assumed to be within VPC (add TLS if crossing networks)

---

## Future Enhancements

- Learning feedback loop: human approvals → update thresholds/rules
- Multi-tenant support: add `org_id` namespace to all Redis keys
- Custom LLM providers: make OpenAI client pluggable
- Event subscriptions: webhook callbacks on resolution
- Batch ingestion: parallel document processing
