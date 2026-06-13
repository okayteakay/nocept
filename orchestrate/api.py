"""orchestrate/api.py

FastAPI application exposing the dashboard, knowledge-base, analytics, and
human-approval endpoints for the autonomous invoice exception resolution agent.

The six-step pipeline itself runs in-process as a LangGraph state machine in
agent/langgraph_agent.py and is triggered asynchronously by worker/tasks.py
after an SAP webhook lands. The remaining `/tools/*` endpoints are the
human approval actions used by the dashboard.

Endpoints
---------
Auth
    POST /auth/token             issue JWT
    POST /auth/refresh           refresh JWT
    GET  /auth/me                current user
Dashboard / search
    POST /exceptions/list        search & filter the exception queue
Human approval
    POST /tools/approve/{id}     manually approve an escalated exception
    POST /tools/reject/{id}      manually reject an escalated exception
Knowledge base
    POST /kb/search/emails       semantic search over emails
    POST /kb/search/transcripts  semantic search over transcripts
    GET  /kb/history/{id}        supplier resolution history summary
Analytics
    GET  /analytics/summary      KPIs, supplier scorecard, trends
"""
from __future__ import annotations

import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import redis as redis_lib
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from audit.audit_logger import AuditEvent, AuditLogger
from auth.jwt_auth import router as auth_router
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import AppConfig, get_settings
from ingestion.json_ingestor import DatasetBundle, load_dataset
from knowledge.client import KnowledgeBaseClient
from knowledge.seeder import seed_knowledge_base
from models.exception import ExceptionState, InvoiceException
from state.machine import VALID_TRANSITIONS
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize per-app state. Resources live on app.state, not in a module-global dict,
    so they survive multi-worker deployments and remain accessible to FastAPI dependencies
    via request.app.state.
    """
    cfg = get_settings()
    cfg.configure_logging()
    r = get_redis_connection(cfg.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")
    dataset = load_dataset()

    kb = KnowledgeBaseClient.from_config(r, cfg)
    seed_counts = seed_knowledge_base(dataset, kb.resolutions, kb.emails, kb.transcripts)
    logger.info(
        "Knowledge base seeded — resolutions: %d, emails: %d, transcripts: %d",
        seed_counts["resolutions"],
        seed_counts["emails"],
        seed_counts["transcripts"],
    )

    app.state.cfg = cfg
    app.state.r = r
    app.state.store = RedisStateStore(r)
    app.state.tavily = TavilyClient(cfg.tavily_api_key)
    app.state.audit = AuditLogger(streams)
    app.state.dataset = dataset
    app.state.kb = kb

    logger.info(
        "Tavily API key loaded: %s",
        f"{cfg.tavily_api_key[:12]}..." if cfg.tavily_api_key else "NOT SET",
    )
    logger.info("Orchestrate API ready — dataset loaded, Redis connected, KB seeded.")
    yield
    # app.state is cleaned up automatically by FastAPI on shutdown


app = FastAPI(
    title="Invoice Exception Resolution — Dashboard & KB API",
    description=(
        "RESTful API for the autonomous LangGraph agent: dashboard search, "
        "human approval, knowledge-base search, and analytics. The six-gate "
        "decision pipeline runs asynchronously via Celery."
    ),
    version="4.0.0",
    lifespan=lifespan,
)

app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Dependencies — read from app.state (per-app, request-safe, multi-worker-safe)
# ---------------------------------------------------------------------------

def get_store(request: Request) -> RedisStateStore:
    return request.app.state.store


def get_redis(request: Request) -> redis_lib.Redis:
    return request.app.state.r


def get_cfg(request: Request) -> AppConfig:
    return request.app.state.cfg


def get_kb(request: Request) -> KnowledgeBaseClient:
    return request.app.state.kb


def get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit


Store = Annotated[RedisStateStore, Depends(get_store)]
R = Annotated[redis_lib.Redis, Depends(get_redis)]
Cfg = Annotated[AppConfig, Depends(get_cfg)]
KB = Annotated[KnowledgeBaseClient, Depends(get_kb)]
Audit = Annotated[AuditLogger, Depends(get_audit)]


# ---------------------------------------------------------------------------
# Human approval
# ---------------------------------------------------------------------------

class ApprovalRequest(BaseModel):
    approved_by: str = Field(description="User ID or email of approver")
    notes: str | None = Field(default=None, description="Optional approval notes")


class ApprovalResponse(BaseModel):
    exception_id: str
    status: str
    message: str


class RejectionRequest(BaseModel):
    rejected_by: str = Field(description="User ID or email of reviewer")
    reason: str = Field(description="Reason for rejection")


class RejectionResponse(BaseModel):
    exception_id: str
    status: str
    message: str


@app.post(
    "/tools/approve/{exception_id}",
    response_model=ApprovalResponse,
    tags=["Human Approval"],
    summary="Manually Approve Escalated Exception",
)
async def approve(
    exception_id: str,
    req: ApprovalRequest,
    store: Store,
    r: R,
    audit: Audit,
) -> ApprovalResponse:
    exc = store.load(exception_id)

    if exc.state not in (ExceptionState.ESCALATED, ExceptionState.PENDING_APPROVAL):
        raise HTTPException(
            400,
            f"Cannot approve exception in state '{exc.state.value}'. "
            f"Only ESCALATED or PENDING_APPROVAL exceptions can be approved.",
        )

    exc.approved_by = req.approved_by
    exc.approval_notes = req.notes
    exc.approval_timestamp = datetime.now(timezone.utc)

    _walk_to_final(store, audit, exception_id, exc.state, ExceptionState.APPROVED)
    exc.state = ExceptionState.APPROVED
    store.save(exc)

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="human_approval",
            actor=req.approved_by,
            details={
                "action": "approved",
                "notes": req.notes or "",
                "timestamp": exc.approval_timestamp.isoformat(),
            },
        )
    )

    return ApprovalResponse(
        exception_id=exception_id,
        status="approved",
        message=f"Exception approved by {req.approved_by}. Notes: {req.notes or 'None'}",
    )


@app.post(
    "/tools/reject/{exception_id}",
    response_model=RejectionResponse,
    tags=["Human Approval"],
    summary="Manually Reject Escalated Exception",
)
async def reject(
    exception_id: str,
    req: RejectionRequest,
    store: Store,
    r: R,
    audit: Audit,
) -> RejectionResponse:
    exc = store.load(exception_id)

    if exc.state not in (ExceptionState.ESCALATED, ExceptionState.PENDING_APPROVAL):
        raise HTTPException(
            400,
            f"Cannot reject exception in state '{exc.state.value}'. "
            f"Only ESCALATED or PENDING_APPROVAL exceptions can be rejected.",
        )

    exc.rejected_by = req.rejected_by
    exc.rejection_reason = req.reason
    exc.rejection_timestamp = datetime.now(timezone.utc)

    _walk_to_final(store, audit, exception_id, exc.state, ExceptionState.REJECTED)
    exc.state = ExceptionState.REJECTED
    store.save(exc)

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="human_rejection",
            actor=req.rejected_by,
            details={
                "action": "rejected",
                "reason": req.reason,
                "timestamp": exc.rejection_timestamp.isoformat(),
            },
        )
    )

    return RejectionResponse(
        exception_id=exception_id,
        status="rejected",
        message=f"Exception rejected by {req.rejected_by}. Reason: {req.reason}",
    )


def _walk_to_final(
    store: RedisStateStore,
    audit: AuditLogger,
    eid: str,
    current: ExceptionState,
    target: ExceptionState,
) -> None:
    """Walk the state machine from *current* to *target* using valid transitions.

    Uses the VALID_TRANSITIONS graph to compute a direct path rather than
    hard-coding intermediate states, so it never attempts an illegal jump.
    Short-circuits only for actual terminal states (APPROVED, REJECTED, RESOLVED).
    """
    if current == target or current in (
        ExceptionState.APPROVED,
        ExceptionState.REJECTED,
        ExceptionState.RESOLVED,
    ):
        return

    # BFS to find the shortest valid path from current → target
    queue: deque[list[ExceptionState]] = deque([[current]])
    visited: set[ExceptionState] = {current}
    path: list[ExceptionState] = []

    while queue:
        route = queue.popleft()
        node = route[-1]
        if node == target:
            path = route[1:]  # skip `current`, it's where we already are
            break
        for neighbor in VALID_TRANSITIONS.get(node, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(route + [neighbor])

    prev = current
    for next_state in path:
        store.transition(eid, next_state)
        audit.log_transition(eid, prev, next_state)
        prev = next_state


# ---------------------------------------------------------------------------
# Dashboard / search / filter
# ---------------------------------------------------------------------------

class ExceptionSummary(BaseModel):
    exception_id: str
    invoice_number: str
    po_number: str
    supplier_name: str
    supplier_id: str
    exception_types: list[str]
    total_variance_usd: float
    variance_percentage: float
    state: str
    created_at: datetime
    approved_by: str | None = None
    rejected_by: str | None = None


class ExceptionListRequest(BaseModel):
    supplier_id: str | None = None
    supplier_name: str | None = None
    invoice_number: str | None = None
    po_number: str | None = None
    status: str | None = None
    variance_min: float | None = None
    variance_max: float | None = None
    limit: int = 50
    offset: int = 0


class ExceptionListResponse(BaseModel):
    exceptions: list[ExceptionSummary]
    total_count: int
    limit: int
    offset: int


@app.post(
    "/exceptions/list",
    response_model=ExceptionListResponse,
    tags=["Dashboard"],
    summary="List Exceptions with Search/Filter",
)
async def list_exceptions(
    req: ExceptionListRequest,
    store: Store,
    r: R,
) -> ExceptionListResponse:
    all_ids: set[str] = set(store.list_queue_ids())
    for state in ExceptionState:
        all_ids.update(store.list_by_state(state))

    filtered_exceptions: list[InvoiceException] = []
    for exception_id in all_ids:
        try:
            exc = store.load(exception_id)

            if req.invoice_number and exc.invoice.invoice_number != req.invoice_number:
                continue
            if req.po_number and exc.purchase_order.po_number != req.po_number:
                continue
            if req.supplier_id and exc.purchase_order.supplier_id != req.supplier_id:
                continue
            if req.supplier_name and req.supplier_name.lower() not in exc.supplier_name.lower():
                continue
            if req.status and exc.state.value != req.status:
                continue
            if req.variance_min is not None and exc.total_variance_usd < req.variance_min:
                continue
            if req.variance_max is not None and exc.total_variance_usd > req.variance_max:
                continue

            filtered_exceptions.append(exc)
        except KeyError:
            continue

    filtered_exceptions.sort(key=lambda e: e.created_at, reverse=True)

    total_count = len(filtered_exceptions)
    paginated = filtered_exceptions[req.offset : req.offset + req.limit]

    summaries = []
    for exc in paginated:
        variance_pct = (
            (abs(exc.total_variance_usd) / exc.purchase_order.total_amount * 100)
            if exc.purchase_order.total_amount > 0
            else 0.0
        )
        summaries.append(
            ExceptionSummary(
                exception_id=exc.exception_id,
                invoice_number=exc.invoice.invoice_number,
                po_number=exc.purchase_order.po_number,
                supplier_name=exc.supplier_name,
                supplier_id=exc.purchase_order.supplier_id,
                exception_types=[t.value for t in exc.exception_types],
                total_variance_usd=exc.total_variance_usd,
                variance_percentage=variance_pct,
                state=exc.state.value,
                created_at=exc.created_at,
                approved_by=exc.approved_by,
                rejected_by=exc.rejected_by,
            )
        )

    return ExceptionListResponse(
        exceptions=summaries,
        total_count=total_count,
        limit=req.limit,
        offset=req.offset,
    )


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

class KBEmailSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    date_filter: str | None = None
    po_filter: str | None = None
    invoice_filter: str | None = None


class KBEmailSearchResponse(BaseModel):
    query: str
    results: list[dict]
    total: int


class KBTranscriptSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    date_filter: str | None = None
    po_filter: str | None = None
    invoice_filter: str | None = None


class KBTranscriptSearchResponse(BaseModel):
    query: str
    results: list[dict]
    total: int


@app.post(
    "/kb/search/emails",
    response_model=KBEmailSearchResponse,
    tags=["Knowledge Base"],
    summary="Semantic Email Search",
)
async def search_emails(req: KBEmailSearchRequest, kb: KB) -> KBEmailSearchResponse:
    results = kb.search_emails(
        query=req.query,
        top_k=req.top_k,
        date_filter=req.date_filter,
        po_filter=req.po_filter,
        invoice_filter=req.invoice_filter,
    )
    return KBEmailSearchResponse(query=req.query, results=results, total=len(results))


@app.post(
    "/kb/search/transcripts",
    response_model=KBTranscriptSearchResponse,
    tags=["Knowledge Base"],
    summary="Semantic Transcript Search",
)
async def search_transcripts(
    req: KBTranscriptSearchRequest,
    kb: KB,
) -> KBTranscriptSearchResponse:
    results = kb.search_transcripts(
        query=req.query,
        top_k=req.top_k,
        date_filter=req.date_filter,
        po_filter=req.po_filter,
        invoice_filter=req.invoice_filter,
    )
    return KBTranscriptSearchResponse(query=req.query, results=results, total=len(results))


@app.get(
    "/kb/history/{supplier_id}",
    tags=["Knowledge Base"],
    summary="Supplier Resolution History",
)
async def supplier_history(supplier_id: str, kb: KB) -> dict:
    return kb.resolutions.supplier_summary(supplier_id)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class AnalyticsSummaryResponse(BaseModel):
    kpis: dict
    supplier_scorecard: list[dict]
    trends: dict
    timestamp: str


@app.get(
    "/analytics/summary",
    response_model=AnalyticsSummaryResponse,
    tags=["Analytics"],
    summary="Get KPI Dashboard Summary",
)
async def get_analytics_summary(
    store: Store,
    days: int = 30,
) -> AnalyticsSummaryResponse:
    from analytics.calculator import AnalyticsCalculator
    from datetime import timedelta

    calculator = AnalyticsCalculator(store)
    date_from = datetime.now(timezone.utc) - timedelta(days=days)
    summary = calculator.get_summary(date_from=date_from)

    return AnalyticsSummaryResponse(
        kpis=summary["kpis"],
        supplier_scorecard=summary["supplier_scorecard"],
        trends=summary["trends"],
        timestamp=summary["timestamp"],
    )


# ---------------------------------------------------------------------------
# Document parsing (OCR + LLM)
# ---------------------------------------------------------------------------

class DocumentParseResponse(BaseModel):
    doc_type: str
    text_chars: int
    model: dict


@app.post(
    "/documents/parse",
    response_model=DocumentParseResponse,
    tags=["Documents"],
    summary="Parse a PDF document via OCR + LLM",
    description=(
        "Upload a PDF (invoice, PO, or GRN) and receive the parsed Pydantic model. "
        "OCR uses Tesseract; structured extraction uses the configured OpenAI-compatible LLM. "
        "The endpoint does NOT auto-create an exception — review the parsed model, then "
        "call /webhook/invoice, /webhook/po, or /webhook/grn to enqueue it."
    ),
)
async def parse_document(
    doc_type: str,
    file: UploadFile = File(...),
) -> DocumentParseResponse:
    """Parse a PDF via OCR and return the structured model.

    ``doc_type`` must be one of: ``invoice``, ``po``, ``grn``.
    """
    doc_type = doc_type.lower()
    if doc_type not in ("invoice", "po", "grn"):
        raise HTTPException(400, f"doc_type must be one of: invoice, po, grn (got {doc_type!r})")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Empty file upload")

    # OCR (Tesseract) — boundary call, may raise
    from ingestion.ocr import extract_text_from_pdf
    text = extract_text_from_pdf(pdf_bytes)

    # LLM parse
    from ingestion.llm_extract import make_llm_extract_fn
    llm_fn = make_llm_extract_fn()

    if doc_type == "invoice":
        from ingestion.ocr import parse_invoice_from_text
        model = parse_invoice_from_text(text, llm_fn)
    elif doc_type == "po":
        from ingestion.ocr import parse_po_from_text
        model = parse_po_from_text(text, llm_fn)
    else:  # grn
        from ingestion.ocr import parse_grn_from_text
        model = parse_grn_from_text(text, llm_fn)

    return DocumentParseResponse(
        doc_type=doc_type,
        text_chars=len(text),
        model=model.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok"}
