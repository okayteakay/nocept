from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

from pydantic import BaseModel

from agent.classifier import classify_exception
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.memo_generator import generate_memo
from agent.researcher import ResearchResult, research_exception
from agent.rules_engine import apply_rules
from audit.audit_logger import AuditLogger
from clients.tavily_client import TavilyClient
from config.settings import AppConfig
from models.exception import ExceptionState, ExceptionType, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from models.resolution import Resolution, ResolutionAction
from state.machine import ExceptionStateMachine
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class PipelineResult(BaseModel):
    """Full output of a single pipeline run."""

    exception: InvoiceException
    resolution: Resolution
    context: SupplierContext
    research: ResearchResult
    elapsed_seconds: float

    model_config = {"arbitrary_types_allowed": True}


def run_pipeline(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    tavily: TavilyClient,
    audit: AuditLogger,
    config: AppConfig,
) -> PipelineResult:
    """Execute the full exception resolution pipeline for one invoice.

    Workflow:
        b. Classify → build InvoiceException, persist RECEIVED
        c. Retrieve supplier context from Redis
        d. Transition to RESEARCHING, run Tavily search
        e. Apply business rules → RulesDecision
        f. Generate ResolutionMemo
        g. Transition to RESOLVED or ESCALATED, persist Resolution

    Every state transition and major step is logged to the audit stream.
    Straight-through invoices (no exception types) skip steps d–e and resolve
    immediately as POLICY_COMPLIANT_VARIANCE / AUTO_APPROVE.

    Args:
        invoice: The supplier invoice to process.
        po: The Purchase Order it references.
        grn: The Goods Receipt Note (or None for missing receipt scenarios).
        store: Redis state store for persistence.
        tavily: Tavily client for external research.
        audit: Audit logger writing to Redis Streams.
        config: AppConfig for thresholds and credentials.

    Returns:
        PipelineResult with the final exception, resolution, and timing.
    """
    raise NotImplementedError


def detect_and_enqueue_exception(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
) -> InvoiceException | None:
    """Step 7 intake helper: detect, persist, and audit a new exception.

    Returns None for straight-through invoices (no exception types).
    """
    classification = classify_exception(
        invoice=invoice,
        po=po,
        grn=grn,
        config=config,
        store=store,
    )
    if not classification.exception_types:
        return None

    exc = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        exception_types=classification.exception_types,
        line_variances=classification.line_variances,
        total_variance_usd=classification.total_variance_usd,
        state=ExceptionState.RECEIVED,
    )
    store.save(exc)
    audit.log_detection(exc)

    logger.info(
        "Detected exception %s (invoice=%s, po=%s, types=%s) and enqueued in state=%s",
        exc.exception_id,
        invoice.invoice_number,
        po.po_number,
        [t.value for t in exc.exception_types],
        exc.state.value,
    )
    return exc
