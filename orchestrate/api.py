"""Invoice exception resolution API with unified ingestion endpoint.

FastAPI application exposing:
- Unified /ingest endpoint for invoice|po|grn in json|text|image|pdf format
- Human approval/rejection endpoints
- Exception listing and search
- Health check

The resolution pipeline runs asynchronously via FastAPI BackgroundTasks.
"""
from __future__ import annotations

import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal

import redis as redis_lib
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agent.langgraph_agent import run_pipeline
from audit.audit_logger import AuditEvent, AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from config.settings import AppConfig, get_settings
from ingestion.normalizer import normalize_document
from models.exception import ExceptionState, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from state.machine import VALID_TRANSITIONS
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize per-app state."""
    cfg = get_settings()
    cfg.configure_logging()
    r = get_redis_connection(cfg.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")

    app.state.cfg = cfg
    app.state.r = r
    app.state.store = RedisStateStore(r)
    app.state.audit = AuditLogger(streams)

    logger.info("API ready — Redis connected, state store initialised.")
    yield


app = FastAPI(
    title="Invoice Exception Resolution API",
    description=(
        "RESTful API for autonomous invoice exception resolution. "
        "Unified ingestion endpoint for documents (invoice/po/grn), "
        "human approval/rejection, and exception management."
    ),
    version="5.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_store(request: Request) -> RedisStateStore:
    return request.app.state.store


def get_redis(request: Request) -> redis_lib.Redis:
    return request.app.state.r


def get_cfg(request: Request) -> AppConfig:
    return request.app.state.cfg


def get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit


Store = Annotated[RedisStateStore, Depends(get_store)]
R = Annotated[redis_lib.Redis, Depends(get_redis)]
Cfg = Annotated[AppConfig, Depends(get_cfg)]
Audit = Annotated[AuditLogger, Depends(get_audit)]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    doc_type: Literal["invoice", "po", "grn"] = Field(
        description="Document type: invoice, po, or grn"
    )
    format: Literal["json", "text", "image", "pdf"] = Field(
        description="Data format"
    )
    data: str | dict | bytes = Field(
        description="Raw document data (string for text, dict/string for json, base64 string for image/pdf)"
    )
    po_number: str | None = Field(
        default=None,
        description="PO number (required for GRN, optional for invoice)"
    )


class IngestResponse(BaseModel):
    status: str
    message: str
    exception_id: str | None = None


async def _run_pipeline_background(
    exception_id: str,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
) -> None:
    """Run the pipeline in the background."""
    try:
        run_pipeline(exception_id, store, audit, config)
    except Exception as e:
        logger.error(f"Background pipeline failed for {exception_id}: {e}", exc_info=True)


@app.post(
    "/ingest",
    response_model=IngestResponse,
    tags=["Ingestion"],
    summary="Ingest Invoice, PO, or GRN",
)
async def ingest(
    req: IngestRequest,
    store: Store,
    r: R,
    cfg: Cfg,
    audit: Audit,
    bg_tasks: BackgroundTasks,
) -> IngestResponse:
    """Unified document ingestion endpoint.

    For PO/GRN: normalizes and caches in Redis.
    For invoice: normalizes, creates exception, runs pipeline in background.

    Returns 200 for PO/GRN, 202 for invoice (Accepted).
    """
    doc_type = req.doc_type.lower()
    data_format = req.format.lower()

    # Normalize the document
    try:
        # Convert data if needed (handle base64 for image/pdf)
        data = req.data
        if data_format in ("image", "pdf") and isinstance(data, str):
            import base64
            data = base64.b64decode(data)

        normalized = normalize_document(doc_type, data_format, data)
        logger.info(f"Successfully normalized {doc_type} document")
    except Exception as e:
        logger.error(f"Normalization failed for {doc_type}: {e}")
        raise HTTPException(
            status_code=422,
            detail=f"Failed to normalize {doc_type}: {str(e)}",
        )

    # Handle PO: cache with 30-day TTL
    if doc_type == "po":
        po: PurchaseOrder = normalized
        try:
            import redis as redis_lib
            po_json_str = po.model_dump_json()
            logger.info(f"★★★ ATTEMPTING TO CACHE PO {po.po_number} ★★★")
            logger.info(f"Caching PO: type={type(po_json_str)}, len={len(po_json_str)}")
            # Use raw redis connection (no decode_responses) to avoid redis-py encoding issues
            raw_r = redis_lib.Redis.from_url(cfg.redis_url, decode_responses=False)
            logger.info(f"★★★ CREATED RAW REDIS CONNECTION ★★★")
            raw_r.execute_command(b"SET", f"po:{po.po_number}".encode(), po_json_str.encode(), b"EX", b"2592000")
            logger.info(f"★★★ SUCCESSFULLY CACHED PO {po.po_number} ★★★")
            audit.log(
                AuditEvent(
                    event_type="po_received",
                    details={
                        "po_number": po.po_number,
                        "supplier_id": po.supplier_id,
                        "total_amount": float(po.total_amount),
                    },
                )
            )
            logger.info(f"PO {po.po_number} cached in Redis")
            return IngestResponse(
                status="stored",
                message=f"PO {po.po_number} received and cached",
            )
        except Exception as e:
            logger.error(f"Failed to cache PO: {e}")
            raise HTTPException(status_code=500, detail="Failed to cache PO")

    # Handle GRN: cache and re-trigger missing exceptions
    if doc_type == "grn":
        grn: GoodsReceiptNote = normalized
        po_number = grn.po_number

        try:
            import redis as redis_lib
            grn_json_str = grn.model_dump_json()
            # Use raw redis connection (no decode_responses) to avoid redis-py encoding issues
            raw_r = redis_lib.Redis.from_url(cfg.redis_url, decode_responses=False)
            raw_r.execute_command(b"SET", f"grn:{po_number}".encode(), grn_json_str.encode(), b"EX", b"2592000")
            audit.log(
                AuditEvent(
                    event_type="grn_received",
                    details={
                        "gr_number": grn.gr_number,
                        "po_number": po_number,
                        "supplier_id": grn.supplier_id,
                    },
                )
            )
            logger.info(f"GRN {grn.gr_number} cached in Redis")

            # Check for MISSING_GOODS_RECEIPT exceptions on this PO and re-trigger
            from models.exception import ExceptionType

            exceptions_received = store.list_by_state(ExceptionState.RECEIVED)
            exceptions_triaged = store.list_by_state(ExceptionState.TRIAGED)
            all_exc_ids = set(exceptions_received + exceptions_triaged)

            retriggered_count = 0
            for exc_id in all_exc_ids:
                try:
                    exc = store.load(exc_id)
                    if (
                        exc.purchase_order.po_number == po_number
                        and ExceptionType.MISSING_GOODS_RECEIPT in exc.exception_types
                    ):
                        exc.grn = grn
                        store.save(exc)
                        bg_tasks.add_task(
                            _run_pipeline_background, exc_id, store, audit, cfg
                        )
                        logger.info(f"Re-triggered exception {exc_id} with GRN {grn.gr_number}")
                        retriggered_count += 1
                except KeyError:
                    pass
                except Exception as e:
                    logger.warning(f"Error re-triggering exception {exc_id}: {e}")

            msg = f"GRN {grn.gr_number} received and cached"
            if retriggered_count > 0:
                msg += f"; re-triggered {retriggered_count} exception(s)"

            return IngestResponse(
                status="stored",
                message=msg,
            )
        except Exception as e:
            logger.error(f"Failed to cache GRN: {e}")
            raise HTTPException(status_code=500, detail="Failed to cache GRN")

    # Handle invoice: create exception and run pipeline
    if doc_type == "invoice":
        invoice: Invoice = normalized

        # Look up PO from Redis
        po_key = f"po:{invoice.po_number}"
        po_json = r.get(po_key)
        if po_json is None:
            logger.warning(f"PO {invoice.po_number} not found in Redis")
            raise HTTPException(
                status_code=422,
                detail=f"PO {invoice.po_number} not found. Call /ingest with PO first.",
            )

        po_str = po_json if isinstance(po_json, str) else po_json.decode()
        po = PurchaseOrder.model_validate_json(po_str)

        # Look up GRN if present
        grn = None
        grn_key = f"grn:{invoice.po_number}"
        grn_json = r.get(grn_key)
        if grn_json:
            grn_str = grn_json if isinstance(grn_json, str) else grn_json.decode()
            grn = GoodsReceiptNote.model_validate_json(grn_str)

        # Create exception
        exc = InvoiceException(
            invoice=invoice,
            purchase_order=po,
            grn=grn,
            state=ExceptionState.RECEIVED,
        )

        try:
            store.save(exc)
            logger.info(f"Exception {exc.exception_id} created and saved")

            audit.log(
                AuditEvent(
                    exception_id=exc.exception_id,
                    event_type="invoice_received",
                    details={
                        "invoice_number": invoice.invoice_number,
                        "po_number": invoice.po_number,
                        "supplier_id": invoice.supplier_id,
                        "total_amount": float(invoice.total_amount),
                    },
                )
            )

            # Enqueue background task
            bg_tasks.add_task(_run_pipeline_background, exc.exception_id, store, audit, cfg)
            logger.info(f"Background pipeline enqueued for {exc.exception_id}")

            return IngestResponse(
                status="accepted",
                message=f"Invoice {invoice.invoice_number} accepted for processing",
                exception_id=exc.exception_id,
            )
        except Exception as e:
            logger.error(f"Failed to create exception: {e}")
            raise HTTPException(status_code=500, detail="Failed to create exception")


# ---------------------------------------------------------------------------
# Human Approval
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


def _walk_to_final(
    store: RedisStateStore,
    audit: AuditLogger,
    eid: str,
    current: ExceptionState,
    target: ExceptionState,
) -> None:
    """Walk state machine from current to target using valid transitions."""
    if current == target or current in (
        ExceptionState.APPROVED,
        ExceptionState.REJECTED,
        ExceptionState.RESOLVED,
    ):
        return

    queue = deque([[current]])
    visited = {current}
    path = []

    while queue:
        route = queue.popleft()
        node = route[-1]
        if node == target:
            path = route[1:]
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


# ---------------------------------------------------------------------------
# Exception Search / Dashboard
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
) -> ExceptionListResponse:
    all_ids = set(store.list_queue_ids())
    for state in ExceptionState:
        all_ids.update(store.list_by_state(state))

    filtered_exceptions = []
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
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok"}
