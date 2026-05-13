# Architecture Documentation: Invoice Exception Resolution System

**Version:** 1.0  
**Date:** May 13, 2026  
**System Name:** Nocept — Autonomous Invoice Exception Resolution Agent

---

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level System Diagram](#high-level-system-diagram)
3. [Component Architecture](#component-architecture)
4. [Data Flow Diagrams](#data-flow-diagrams)
5. [State Machine & Lifecycle](#state-machine--lifecycle)
6. [Technology Stack](#technology-stack)
7. [Security Architecture](#security-architecture)
8. [Performance Characteristics](#performance-characteristics)
9. [Scalability Considerations](#scalability-considerations)
10. [Deployment Architecture](#deployment-architecture)

---

## System Overview

### **Purpose**

Nocept automatically triages and resolves invoice-to-PO mismatches in enterprise Accounts Payable workflows. It eliminates ~70% of routine manual exception reviews through intelligent decision gates, while escalating complex cases to human managers with evidence-based recommendations.

### **Key Outcomes**

- **60–80% Auto-Resolution Rate:** Invoices matching tolerance, history, comms, or research auto-approve without human involvement
- **Evidence-Based Escalations:** Remaining 20–40% escalated with supporting evidence (emails, web research, historical data)
- **Immutable Audit Trail:** All decisions logged for compliance (SOX, audit requirements)
- **Cost Savings:** Prevents overpayments (duplicates, unauthorized variances) worth ~$100k annually for typical enterprise

### **Core Philosophy**

1. **Conservative Auto-Approval:** System only auto-approves when evidence is strong (confidence 0.75+)
2. **Deterministic Gates:** Six sequential gates; first one that fires determines outcome (no ambiguity)
3. **Explainability:** Every decision backed by evidence (variance breakdown, linked comms, research findings)
4. **Human Partnership:** Managers make final decisions; system provides analysis and automation

---

## High-Level System Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         INGESTION LAYER                                    │
│                                                                            │
│  SAP S/4HANA Webhook      JSON Dataset          Manual Upload             │
│  (Invoices, POs, GRNs)    (Test Data)           (Future)                  │
│        │                      │                     │                      │
│        └──────────────────────┼─────────────────────┘                      │
│                               ▼                                            │
│                    Webhook Handler / Ingestor                              │
│                    (Normalization, Validation)                             │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    CACHE & PERSISTENCE LAYER                               │
│                                                                            │
│  Redis (Cluster/Standalone)                                               │
│  ├─ Exception Queue (FIFO)                                                │
│  ├─ Exception State Store (by ID)                                         │
│  ├─ State Machine (transitions)                                           │
│  ├─ Historical Approvals (lookup)                                         │
│  ├─ Audit Trail (immutable stream)                                        │
│  └─ Supplier Context Cache                                                │
│                                                                            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                      AGENT PIPELINE (Core Logic)                           │
│                                                                            │
│  [1] CLASSIFIER                                                            │
│      ├─ Detect exception type (price, qty, duplicate, etc.)               │
│      ├─ Compute variance (invoice vs. PO)                                 │
│      ├─ Extract SKU, qty, prices                                          │
│      └─ → Output: ExceptionRecord                                         │
│                                                                            │
│  [2] TOLERANCE GATE                                                        │
│      ├─ Check: variance % <= 1%?                                          │
│      └─ → YES: AUTO_APPROVE (confidence 1.0), SKIP rest                   │
│                                                                            │
│  [3] HISTORY GATE                                                          │
│      ├─ Query Redis for similar approved cases (supplier, type, range)     │
│      ├─ Match: supplier X, price variance 2-3%, approved 5 times?         │
│      └─ → YES: AUTO_APPROVE (confidence 0.90), SKIP rest                  │
│                                                                            │
│  [4] COMMUNICATIONS GATE                                                   │
│      ├─ Link exception to emails/transcripts (supplier, invoice #)        │
│      ├─ LLM analysis: Does communication confirm variance?                │
│      ├─ Threshold: confidence >= 0.75?                                    │
│      └─ → YES: AUTO_APPROVE (confidence 0.85), SKIP rest                  │
│                                                                            │
│  [5] RESEARCH GATE                                                         │
│      ├─ Web search (Tavily) for corroborating evidence                    │
│      ├─ Example: Supplier price list matching invoice price               │
│      ├─ Threshold: confidence >= 0.70?                                    │
│      └─ → YES: AUTO_APPROVE (confidence 0.80), SKIP rest                  │
│                                                                            │
│  [6] ESCALATE GATE                                                         │
│      ├─ No gate fired → insufficient evidence                             │
│      ├─ Route to human manager                                            │
│      ├─ Generate memo with all evidence                                   │
│      └─ → PENDING_APPROVAL (awaits manager decision)                      │
│                                                                            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                      MEMO & EVIDENCE ASSEMBLY                              │
│                                                                            │
│  Resolution Memo Generator                                                │
│  ├─ Summarize variance breakdown                                          │
│  ├─ List all evidence (comms, research, history)                          │
│  ├─ Show confidence reasoning                                             │
│  └─ Format for manager review (web UI or email)                           │
│                                                                            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD & APPROVAL LAYER                              │
│                                                                            │
│  Streamlit Web Dashboard                                                  │
│  ├─ Exception Queue (searchable, filterable)                              │
│  ├─ Detail View (variance, evidence, audit trail)                         │
│  ├─ Approval Workflow (manager: approve/reject)                           │
│  ├─ Analytics (KPIs, supplier scorecards, spend variance)                 │
│  ├─ Rules Management (create/edit automation rules)                       │
│  └─ Role-Based Access (clerk, manager, finance)                           │
│                                                                            │
│  REST API (FastAPI)                                                        │
│  ├─ /tools/intake (exception creation)                                    │
│  ├─ /tools/tolerance/{id} (gate processing)                               │
│  ├─ /tools/history/{id}                                                   │
│  ├─ /tools/communications/{id}                                            │
│  ├─ /tools/research/{id}                                                  │
│  ├─ /tools/resolve/{id} (finalization)                                    │
│  ├─ /approvals/approve (manager approval)                                 │
│  ├─ /approvals/reject (manager rejection)                                 │
│  ├─ /analytics/kpis (fetch metrics)                                       │
│  ├─ /analytics/suppliers (scorecard lookup)                               │
│  └─ /rules/* (CRUD operations on rules)                                   │
│                                                                            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    EXTERNAL INTEGRATIONS                                   │
│                                                                            │
│  LLM Services (OpenAI API)                                                │
│  ├─ Classify exceptions                                                   │
│  ├─ Analyze communications (emails, transcripts)                          │
│  └─ Generate resolution memos                                             │
│                                                                            │
│  Web Search API (Tavily)                                                  │
│  ├─ Research supplier prices, policies                                    │
│  └─ Corroborate variance with public sources                              │
│                                                                            │
│  Knowledge Base (Redis-backed)                                            │
│  ├─ Historical resolutions (similarity search)                            │
│  ├─ Email database (linked to exceptions)                                 │
│  └─ Call transcript database (indexed)                                    │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Architecture

### **1. Ingestion Layer**

**Responsibility:** Accept invoices from SAP S/4HANA or test data; validate and normalize.

**Components:**

- **WebhookHandler** (`ingestion/webhook_handler.py`)
  - Receives POST requests from SAP webhook
  - Validates JSON payload structure
  - Normalizes fields (dates, amounts, SKU formats)
  - Creates `InvoiceException` object
  - Queues to Redis for processing

- **JSONIngestor** (`ingestion/json_ingestor.py`)
  - Loads test dataset from JSON files
  - Parses invoices, POs, goods receipts
  - Generates test fixtures for demos

- **SAPMapper** (`ingestion/sap_mapper.py`)
  - Maps S/4HANA field names to system schema
  - Handles currency conversion (if needed)
  - Extracts line items, taxes, discounts

- **OCRHandler** (`ingestion/ocr.py`)
  - Future: Extract data from receipt images
  - Currently placeholder for extension

**Data Flow:**
```
SAP S/4HANA
    │
    ├─ POST /webhook/invoice
    │   ├─ Payload: {invoice, po, grn}
    │   ├─ WebhookHandler validates & normalizes
    │   ├─ Creates InvoiceException (state=RECEIVED)
    │   └─ Pushes to Redis queue
    │
    ▼
Redis Queue
    ├─ FIFO: ~1,000 exceptions/day capacity
    └─ Consumed by Agent Pipeline
```

---

### **2. State Management Layer**

**Responsibility:** Persist exception state, enforce valid transitions, track history.

**Components:**

- **ExceptionStateMachine** (`state/machine.py`)
  - Enforces valid state transitions
  - Prevents illegal transitions (e.g., RESOLVED → TRIAGED)
  - Raises `InvalidTransitionError` if violated

- **RedisStateStore** (`state/redis_backend.py`)
  - Persists exception state to Redis
  - Key structure: `exc:{exception_id}` → JSON (InvoiceException)
  - Retrieval by state: `exc:state:{state}` → set of IDs
  - FIFO queue: `exc:queue` → list of exception IDs

- **AuditLogger** (`audit/audit_logger.py`)
  - Logs all state transitions
  - Records approvals, rejections, overrides
  - Immutable stream in Redis: `ap:audit:events`
  - Used for compliance & debugging

**State Lifecycle:**
```
RECEIVED
   │
   ▼
TRIAGED
   │
   ├─▶ RESEARCHING ─┐
   │                │
   ├─▶ PENDING_APPROVAL ─┬─▶ APPROVED ✓
   │                      ├─▶ REJECTED ✓
   │                      └─▶ ESCALATED ─┐
   │                                      │
   ├─▶ ESCALATED ◀──────────────────────┘
   │
   └─▶ RESOLVED ✓

Terminal states: APPROVED, REJECTED, RESOLVED
```

---

### **3. Agent Pipeline (Core Logic)**

**Responsibility:** Six-gate decision logic for exception resolution.

**Components:**

- **Classifier** (`agent/classifier.py`)
  - Input: Invoice + PO + GRN
  - Uses LLM to detect exception types
  - Computes line-item variances
  - Returns: `ExceptionType[]`, `LineItemVariance[]`, `total_variance_usd`

- **Rules Engine** (`agent/rules_engine.py`)
  - Implements six deterministic gates
  - Each gate: condition → decision (auto-approve/reject/escalate)
  - Gates are stateless; no side effects (pure functions)

  **Gate 1: Classification**
  ```python
  if exception_type == "DUPLICATE":
      decision = AUTO_REJECT (prevents overpayment)
      confidence = 1.0
  elif exception_type == "NONE":
      decision = AUTO_APPROVE (straight-through)
      confidence = 1.0
  else:
      # Continue to gate 2
  ```

  **Gate 2: Tolerance**
  ```python
  if variance_pct <= 1.0:
      decision = AUTO_APPROVE
      confidence = 1.0
  else:
      # Continue to gate 3
  ```

  **Gate 3: History**
  ```python
  similar_cases = query_historical_approvals(supplier, exception_type, variance_range)
  if len(similar_cases) > 0:
      decision = AUTO_APPROVE
      confidence = 0.90
  else:
      # Continue to gate 4
  ```

  **Gate 4: Communications**
  ```python
  emails = retrieve_linked_emails(exception)
  transcripts = retrieve_linked_transcripts(exception)
  llm_analysis = analyze_comms(emails, transcripts, variance)  # Returns: score (0-1)
  if llm_analysis.confidence >= 0.75:
      decision = AUTO_APPROVE
      confidence = 0.85
  else:
      # Continue to gate 5
  ```

  **Gate 5: Research**
  ```python
  search_results = tavily_search(supplier_name, product_name, price)
  evidence_score = score_research_findings(search_results, variance)  # Returns: score
  if evidence_score >= 0.70:
      decision = AUTO_APPROVE
      confidence = 0.80
  else:
      # Continue to gate 6
  ```

  **Gate 6: Escalate**
  ```python
  decision = ESCALATE_TO_HUMAN
  confidence = null
  # Memo generated with all evidence
  ```

- **HistoryChecker** (`agent/history_checker.py`)
  - Queries Redis for historical approvals
  - Similarity matching: same supplier, same exception type, variance within ±5%
  - Returns: list of matching cases with approval metadata

- **CommsChecker** (`agent/comms_checker.py`)
  - Links exception to related emails/transcripts via supplier name, invoice #
  - Calls LLM to analyze if comms confirm variance
  - Returns: boolean (confirmed yes/no) + confidence score

- **Researcher** (`agent/researcher.py`)
  - Calls Tavily API to search for corroborating evidence
  - Example queries:
    - "Acme Corp current pricing 2024 2025"
    - "Widget Inc shipping surcharge"
  - Scores results for relevance (0-1)
  - Returns: list of research findings + combined confidence

- **ContextRetriever** (`agent/context_retriever.py`)
  - Builds supplier context summary
  - Queries: historical variance avg, invoice volume, past issues
  - Returns: `SupplierContext` object with summary stats

- **MemoGenerator** (`agent/memo_generator.py`)
  - Assembles all evidence into readable memo
  - Formats: variance breakdown, linked comms, research findings, confidence reasoning
  - Output: `ResolutionMemo` (HTML + plain text)

---

### **4. Knowledge Base Layer**

**Responsibility:** Store and retrieve historical data (approvals, comms, evidence).

**Components:**

- **KnowledgeBaseClient** (`knowledge/client.py`)
  - Interface to Redis-backed knowledge base
  - Provides: `resolutions`, `emails`, `transcripts` sub-clients

- **Seeder** (`knowledge/seeder.py`)
  - Populates knowledge base on startup
  - Indexes: historical resolutions, sample emails, transcripts
  - Pre-computed similarity embeddings (future)

**Data Stored:**

1. **Historical Resolutions**
   - Key: `kb:resolutions:{supplier}:{exception_type}:{variance_range}`
   - Value: JSON array of past approvals
   - Used by HistoryChecker

2. **Emails**
   - Key: `kb:emails:{supplier}:{invoice_id}`
   - Value: Email text, summary, metadata
   - Used by CommsChecker

3. **Transcripts**
   - Key: `kb:transcripts:{supplier}:{call_date}`
   - Value: Transcript text, summary, participants
   - Used by CommsChecker

---

### **5. Dashboard & UI Layer**

**Responsibility:** User-facing interfaces for viewing, filtering, approving exceptions.

**Components:**

- **Streamlit Dashboard** (`dashboard/app.py`)
  - Single-page app with multi-role views
  - Pages:
    - Exception Queue (searchable, filterable)
    - Exception Detail (variance, evidence, audit trail)
    - Pending Approvals (manager-only)
    - Analytics (finance-only)
    - Rules Management (manager-only)
  - Authentication: JWT middleware via `auth/jwt_auth.py`
  - Responsive design (desktop + mobile)

- **FastAPI REST API** (`orchestrate/api.py`)
  - RESTful endpoints for all operations
  - Stateless design (no session affinity needed)
  - OpenAPI/Swagger docs auto-generated
  - Error handling: 400/401/403/404/500 with descriptive messages

---

### **6. Analytics Layer**

**Responsibility:** Calculate KPIs, supplier scorecards, spend variance.

**Components:**

- **AnalyticsCalculator** (`analytics/calculator.py`)
  - Methods: `calculate_kpis()`, `supplier_scorecard()`, `spend_variance_report()`
  - Filters by date range, supplier, exception type
  - Computes: auto-approval rate, cost saved, SLA compliance, trend analysis
  - Returns: JSON dict for dashboard charts

---

## Data Flow Diagrams

### **Flow 1: Exception Ingestion & Pipeline**

```
[SAP S/4HANA]
    │
    │ POST {invoice, po, grn}
    ▼
[WebhookHandler]
    ├─ Validate schema
    ├─ Normalize fields
    └─ Create InvoiceException
         state: RECEIVED
         created_at: now
    │
    ▼
[Redis Queue: exc:queue]
    │
    ├──────────────────────────────────────────────────┐
    │                                                  │
    ▼                                                  ▼
[Triaged & Queued]                              [Agent Pipeline Processor]
state: TRIAGED                                  (async consumer)
    │
    ├─────────────────────────────────────────────────────┐
    │                                                     │
    ▼                                                     ▼
[Classifier]                                      [RulesEngine]
├─ LLM: detect exception type                    │
├─ Compute variance                              ├─[Gate 1: Classify]
└─ Output: exception_types, variances            │  ├─ Duplicate? → AUTO_REJECT
                                                 │  └─ No exception? → AUTO_APPROVE
                                                 │
                                                 ├─[Gate 2: Tolerance]
                                                 │  ├─ variance% ≤ 1%? → AUTO_APPROVE
                                                 │  └─ else → continue
                                                 │
                                                 ├─[Gate 3: History]
                                                 │  ├─ Query similar cases
                                                 │  ├─ Found? → AUTO_APPROVE
                                                 │  └─ else → continue
                                                 │
                                                 ├─[Gate 4: Comms]
                                                 │  ├─ Link emails/transcripts
                                                 │  ├─ LLM analysis
                                                 │  ├─ confidence ≥ 0.75? → AUTO_APPROVE
                                                 │  └─ else → continue
                                                 │
                                                 ├─[Gate 5: Research]
                                                 │  ├─ Tavily web search
                                                 │  ├─ Score findings
                                                 │  ├─ confidence ≥ 0.70? → AUTO_APPROVE
                                                 │  └─ else → continue
                                                 │
                                                 └─[Gate 6: Escalate]
                                                    ├─ No gate fired
                                                    └─ ESCALATE_TO_HUMAN
                                                       state: PENDING_APPROVAL
    │
    ▼
[MemoGenerator]
├─ Assemble variance breakdown
├─ Link all evidence
├─ Format for manager review
└─ Output: ResolutionMemo
    │
    ▼
[Redis: Exception Store]
├─ Key: exc:{exception_id}
├─ State: APPROVED/REJECTED/RESOLVED/PENDING_APPROVAL
├─ Metadata: created_at, updated_at, decision, confidence
└─ Audit trail entry
    │
    ▼
[Dashboard]
├─ Auto-approved: appears in "Resolved"
├─ Escalated: appears in "Pending Approvals"
└─ Ready for user action
```

---

### **Flow 2: Manager Approval Decision**

```
[Manager Dashboard]
    │
    ├─ View "Pending Approvals" tab
    │
    ▼
[Exception Detail View]
    ├─ Variance summary
    ├─ Linked comms (emails, transcripts)
    ├─ Research findings
    ├─ Historical precedent
    └─ System recommendation + confidence
    │
    ├─ [Manager Reviews Evidence]
    │
    ▼
[Decision: Approve OR Reject]
    │
    ├─ Click "Approve" button
    │  │
    │  ▼
    │ [Approval Handler]
    │ ├─ Update state: APPROVED
    │ ├─ Set: approved_by, approval_notes, approval_timestamp
    │ ├─ Optionally create rule (automation)
    │ ├─ Audit log entry
    │ └─ Redis update
    │      │
    │      ▼
    │   [Invoice Marked Paid]
    │   └─ Sync to SAP (future)
    │
    └─ Click "Reject" button
       │
       ▼
      [Rejection Handler]
      ├─ Update state: REJECTED
      ├─ Set: rejected_by, rejection_reason, rejection_timestamp
      ├─ Audit log entry
      └─ Redis update
           │
           ▼
        [Invoice Held for Correction]
        └─ Notify supplier (future)
```

---

### **Flow 3: Analytics & Reporting**

```
[Finance Dashboard / API Call]
    │
    ▼
[AnalyticsCalculator.calculate_kpis()]
    │
    ├─ Retrieve all exceptions from Redis
    │  └─ Filter by date range, supplier, type
    │
    ├─ Compute metrics:
    │  ├─ Total exceptions
    │  ├─ Auto-approval rate %
    │  ├─ Manual approvals count
    │  ├─ Rejections count
    │  ├─ Avg resolution time (hours)
    │  ├─ Cost at risk (sum of variances)
    │  ├─ Cost saved (sum of rejected variances)
    │  └─ SLA compliance %
    │
    ▼
[Output: KPI Summary]
    ├─ Total Exceptions: 142
    ├─ Auto-Approval Rate: 72%
    ├─ Cost at Risk: $87,400
    ├─ Cost Saved: $18,200
    └─ Avg Resolution: 2.3 hours
    │
    ▼
[Supplier Scorecards]
    ├─ For each supplier:
    │  ├─ Exception rate % (exceptions / invoices)
    │  ├─ Exception types (price, qty, duplicate, etc.)
    │  ├─ Variance trend (improving/declining)
    │  └─ Risk level (low/medium/high)
    │
    ▼
[Spend Variance Report]
    ├─ Breakdown by exception type
    ├─ Breakdown by supplier
    ├─ Trend chart (6 months)
    └─ Financial impact (approved vs rejected)
    │
    ▼
[Dashboard Charts & Tables]
    └─ Ready for executive viewing
```

---

## State Machine & Lifecycle

### **Valid State Transitions**

```
RECEIVED → TRIAGED → {RESEARCHING, PENDING_APPROVAL, ESCALATED, RESOLVED}
             │
             ├─▶ RESEARCHING → {PENDING_APPROVAL, ESCALATED}
             │
             ├─▶ PENDING_APPROVAL → {APPROVED, REJECTED, ESCALATED, RESOLVED}
             │
             └─▶ ESCALATED → {APPROVED, REJECTED, RESOLVED}

Terminal states: APPROVED, REJECTED, RESOLVED
```

### **Typical Exception Lifecycle**

**Scenario 1: Auto-Approved (via Tolerance Gate)**

```
Time   State                 Agent/User Action          Notes
─────────────────────────────────────────────────────────────────
0:00   RECEIVED             SAP webhook received       Just ingested
0:01   TRIAGED              Classifier runs            Exception type detected
0:02   → RESOLVED           Tolerance gate fires       Variance ≤ 1%
                            (AUTO_APPROVE)            Confidence: 1.0
                                                      No manager needed
```

**Scenario 2: Escalated (No Gate Fires)**

```
Time   State                 Agent/User Action          Notes
─────────────────────────────────────────────────────────────────
0:00   RECEIVED             SAP webhook received       Just ingested
0:01   TRIAGED              Classifier runs            Exception detected
0:02   RESEARCHING          Agent gathers evidence     Runs all 5 gates
0:10                        (comms, research, etc.)    No gate fires
0:11   → PENDING_APPROVAL   Escalated to manager       Awaits approval
8:00   → APPROVED           Manager approves           Decision made
                            (after reviewing memo)     Confidence: manager's choice
```

**Scenario 3: Rejected**

```
Time   State                 Agent/User Action          Notes
─────────────────────────────────────────────────────────────────
0:00   RECEIVED             SAP webhook received       Just ingested
0:01   TRIAGED              Classifier runs            Duplicate detected
0:02   → REJECTED           Auto-reject (gate 1)       Prevents overpayment
                                                       Confidence: 1.0
                            (No manager decision)      No human action needed
```

### **State Persistence**

- **In-Memory:** ExceptionStateMachine class (ephemeral)
- **Persistent:** RedisStateStore (durable)
- **Audit Trail:** AuditLogger streams to Redis (immutable)

---

## Technology Stack

### **Backend Framework**

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Web Framework | FastAPI (Python 3.10+) | REST API, type safety, auto-docs |
| Dashboard | Streamlit | Interactive web UI, charts |
| State Machine | Custom class | Exception lifecycle enforcement |
| Job Queue | Redis Streams | Async task processing |
| Data Store | Redis | Primary persistence (exceptions, state) |
| LLM API | OpenAI (compatible) | Exception analysis, comms evaluation |
| Web Search | Tavily Search API | Corroborating evidence |

### **Data Formats**

| Format | Usage |
|--------|-------|
| JSON | API payloads, config files |
| Pydantic Models | Type-safe data validation |
| Python Enums | Exception types, states |
| CSV | Data export (analytics) |
| HTML | Email notifications, memo formatting |

### **Frontend**

| Component | Technology |
|-----------|-----------|
| Dashboard UI | Streamlit (Python-based) |
| Charts | Plotly (interactive) |
| Authentication | JWT tokens, SSO integration |
| Styling | Streamlit themes (light/dark) |

### **Deployment**

| Component | Technology |
|-----------|-----------|
| Container | Docker (Linux base) |
| Orchestration | Kubernetes (future) or Docker Compose |
| CI/CD | GitHub Actions |
| Logging | Python logging + Redis streams |
| Monitoring | Prometheus metrics (future) |

### **External Services**

| Service | Provider | Purpose |
|---------|----------|---------|
| LLM | OpenAI | Exception classification, comms analysis |
| Search | Tavily | Web research for corroboration |
| Authentication | Okta/Azure AD | SSO (optional) |
| Email | SendGrid (future) | Supplier notifications |

---

## Security Architecture

### **Authentication & Authorization**

```
[User Login Request]
    │
    ├─ Email + Password (or SSO)
    │
    ▼
[JWT Auth Handler] (auth/jwt_auth.py)
    ├─ Validate credentials
    ├─ Issue JWT token (expires: 8 hours)
    ├─ Token includes: user_id, email, role
    └─ Return: token
    │
    ▼
[Dashboard / API]
    ├─ Check JWT in request header
    ├─ Validate signature (secret key)
    ├─ Extract role from token
    ├─ Apply role-based access control
    └─ Allow/Deny request
    │
▼
Role-Based Views:
├─ AP Clerk: Read-only (exceptions, filters, export)
├─ AP Manager: Approve/reject, create rules
└─ Finance: Analytics, KPIs, supplier scorecards
```

### **Data Protection**

1. **Encryption at Rest**
   - Redis: Optional RDB encryption (deployment config)
   - Environment variables: Store in `.env.local` (gitignored)

2. **Encryption in Transit**
   - HTTPS: All API calls encrypted (TLS 1.2+)
   - Webhook: Signed requests (HMAC validation, future)

3. **Access Control**
   - Dashboard: JWT-gated (login required)
   - API: Role-based decorators (FastAPI Depends)
   - Supplier data: Scoped to user's assigned vendors (future)

4. **Audit Trail**
   - Every action logged: user, action, timestamp, impact
   - Immutable stream (Redis Streams, no deletion)
   - Retention: Indefinite (compliance requirement)

### **Secrets Management**

```
Environment Variables (.env.local, not in git):
├─ OPENAI_API_KEY
├─ TAVILY_API_KEY
├─ REDIS_URL
├─ JWT_SECRET_KEY
├─ AUTH_PROVIDER (Okta/AzureAD)
└─ Other sensitive config
```

### **API Security**

- **CORS:** Configured for dashboard origin only
- **Rate Limiting:** 100 req/min per IP (future)
- **Input Validation:** Pydantic models validate all inputs
- **SQL Injection:** N/A (Redis, not SQL)
- **CSRF Protection:** JWT tokens (no cookies)

---

## Performance Characteristics

### **Throughput**

| Operation | Latency | Throughput |
|-----------|---------|-----------|
| Exception Ingestion | <100ms | ~1,000 exc/day |
| Classifier (LLM) | 1–3 sec | ~500 exc/day sequential |
| Rules Gates (1–5) | <500ms | ~10,000 exc/day |
| Manager Approval | <100ms | Limited by human speed (~20 per hour) |
| Analytics Calc (1,000 exc) | <500ms | Per-request, cached |

### **Storage**

| Data Type | Size | Retention |
|-----------|------|-----------|
| Exception Record | ~5 KB (JSON) | Indefinite |
| Audit Log Entry | ~500 B | Indefinite |
| Memo (HTML) | ~10 KB | Indefinite |
| 1,000 exceptions | ~50 MB | N/A |

**Redis Memory Estimate:**
- 1,000 exceptions: ~50 MB
- 5 years of audit trail: ~1 GB
- Total: ~1–2 GB (comfortable on modern Redis)

### **Bottlenecks & Optimization**

1. **LLM Latency (Classifier & CommsChecker)**
   - Bottleneck: 1–3 sec per exception
   - Mitigation: Batch classification (future), async queue

2. **Tavily Search API**
   - Bottleneck: 5–10 sec per research call
   - Mitigation: Cache results, reuse for similar queries

3. **Dashboard Load (1,000+ exceptions)**
   - Bottleneck: Rendering large lists
   - Mitigation: Pagination, lazy-loading, filters

4. **Audit Trail Queries**
   - Bottleneck: Scanning immutable stream
   - Mitigation: Index by exception_id, timestamp (Redis Streams native)

---

## Scalability Considerations

### **Vertical Scaling (Single Machine)**

**Current:** ~1,000 exceptions/day on single Redis instance + 1 API server

**Limits:**
- Redis max memory: 16–64 GB (adjust retention)
- CPU: 4 cores sufficient for current load
- Network: <100 Mbps required

**When to scale up:** >5,000 exceptions/day

### **Horizontal Scaling (Distributed)**

**Architecture:**
```
[Multiple SAP Webhooks / Ingestion Points]
         │
         ▼
[Load Balancer] (Round-robin or sticky sessions not needed)
         │
    ┌────┴────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼
[API 1]   [API 2]   [API 3]   [API 4]  (Stateless FastAPI servers)
    │         │         │         │
    └────┬────┴────┬────┴────┬────┘
         │
         ▼
[Redis Cluster]  (Master + replicas)
├─ Sharding by exception_id hash
├─ Replication for HA
└─ Sentinel for failover
```

**Scaling Steps:**
1. Move to Redis Cluster (from standalone)
2. Deploy multiple FastAPI instances
3. Add load balancer (nginx, HAProxy)
4. Scale LLM calls (use OpenAI batch API, future)
5. Cache research results (Redis with TTL)

### **Database Sharding (Future)**

If Redis reaches limits (>100k exceptions):
1. Add PostgreSQL for historical data (archive old exceptions)
2. Keep recent 6 months in Redis (hot cache)
3. Query archive for analytics (slower but cheaper)

### **Async Processing**

Current: Synchronous (blocking) gates

Future: Async gates with job queues
```
[Exception Ingested]
    │
    ├─ [Gate 1: Classify] → Async task (Celery/Kafka)
    ├─ [Gate 2: Tolerance] → Sync (fast)
    ├─ [Gate 3: History] → Sync (Redis lookup)
    ├─ [Gate 4: Comms] → Async task (OpenAI)
    ├─ [Gate 5: Research] → Async task (Tavily)
    └─ [Gate 6: Escalate] → Sync (finalize)
    
    Result: Sub-second initial response, background processing
```

---

## Deployment Architecture

### **Local Development**

```
docker-compose up -d

Services:
├─ redis:6.2 (persistence: YES)
├─ fastapi:app (port 8000)
├─ streamlit:dashboard (port 8501)
└─ openai/tavily APIs (external)
```

### **Staging (Cloud)**

```
Docker image built → Push to registry

Deployment:
├─ Docker Compose on single VM (temp)
├─ Or Kubernetes (preferred):
│  ├─ FastAPI deployment (3 replicas)
│  ├─ Redis StatefulSet (master + 2 replicas)
│  ├─ Streamlit service
│  └─ Ingress (expose /api and /dashboard)
│
├─ Load testing (Locust, 1,000 concurrent)
└─ Validation: All gates fire correctly
```

### **Production (Enterprise)**

```
Ingestion:
├─ SAP S/4HANA webhook → API endpoint

Processing:
├─ Kubernetes (managed: EKS/GKE/AKS)
├─ FastAPI: 5+ replicas, auto-scale on CPU/memory
├─ Async jobs: Celery workers (if implemented)

Persistence:
├─ Redis Cluster: 3+ nodes, replication, persistence
├─ Backup: Daily snapshots to S3/blob storage
├─ DR: Multi-region failover (if required)

UI:
├─ Streamlit: 2+ replicas behind load balancer
├─ CDN: Serve static assets (future)

Monitoring:
├─ Prometheus metrics (exception count, latency)
├─ ELK stack (logs aggregation)
├─ PagerDuty alerts (failures)

Compliance:
├─ TLS 1.2+ encryption
├─ Role-based access (Okta/Azure AD)
├─ Audit trail logged (immutable)
├─ Data retention: 7 years (financial records)
```

### **Backup & Disaster Recovery**

**Backup Strategy:**
- Daily Redis snapshots (RDB) → S3/blob storage
- Retention: 30 days (can recover from past month)
- Encryption: AES-256 in transit and at rest

**Disaster Recovery:**
- RTO (Recovery Time Objective): <1 hour
- RPO (Recovery Point Objective): <1 hour (last snapshot)
- Restore procedure: Load latest snapshot, verify data, switch DNS

**Testing:**
- Monthly DR drill (restore from backup, validate)

---

## Monitoring & Observability

### **Key Metrics**

**Business Metrics:**
- Exceptions per day
- Auto-approval rate (%)
- Cost saved per day ($)
- Average resolution time (hours)
- SLA compliance (%)

**Technical Metrics:**
- API response time (p50, p95, p99)
- Exception processing latency
- Redis memory usage
- LLM API call count & cost
- Error rate (4xx, 5xx)

### **Alerting**

| Alert | Threshold | Action |
|-------|-----------|--------|
| API Latency High | p99 > 5 sec | Page on-call, investigate |
| Error Rate | >5% of requests | Page on-call, check logs |
| Redis Memory | >80% capacity | Scale up or archive old data |
| LLM API Quota | >80% monthly | Optimize batch calls, budget review |
| Exception Queue Backlog | >10,000 pending | Scale API servers |

### **Logging**

```
Level    Component              Message
─────────────────────────────────────────────────────
INFO     WebhookHandler        "Received exception: INV-12345"
DEBUG    Classifier            "Exception type: PRICE_VARIANCE, confidence: 0.92"
INFO     RulesEngine           "Gate 2 fired: TOLERANCE auto-approved"
INFO     AuditLogger           "Manager john.doe approved exc-uuid"
ERROR    TavilyClient          "Search failed: API rate limit"
WARNING  RedisStateStore       "Slow query: list_by_state(PENDING_APPROVAL) took 5s"
```

---

## API Specification Summary

### **Core Endpoints**

| Method | Endpoint | Role | Purpose |
|--------|----------|------|---------|
| POST | `/webhook/exception` | System | Ingest new exception |
| GET | `/exceptions/{id}` | Clerk+ | View exception detail |
| GET | `/exceptions` | Clerk+ | List exceptions (search, filter) |
| POST | `/approvals/approve` | Manager | Approve exception |
| POST | `/approvals/reject` | Manager | Reject exception |
| POST | `/rules` | Manager | Create automation rule |
| GET | `/analytics/kpis` | Finance | Fetch KPI metrics |
| GET | `/analytics/suppliers` | Finance | Fetch supplier scorecards |

(Full spec in `API_REFERENCE.md`)

---

## Conclusion

Nocept's architecture is designed for:
- **Efficiency:** Auto-resolves 60–80% of exceptions, freeing AP staff
- **Transparency:** Evidence-based decisions, full audit trail
- **Scalability:** Stateless APIs, persistent Redis, async-ready
- **Reliability:** Error handling, rollback capability, disaster recovery
- **Security:** JWT auth, role-based access, encrypted transit & storage

The system is production-ready for small-to-medium enterprises (~5k invoices/month). Horizontal scaling and async processing planned for larger deployments.

---

**Document prepared:** May 13, 2026  
**For:** Invoice Exception Resolution System (Nocept)  
**Contact:** architecture@nocept-system.internal
