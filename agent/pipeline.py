from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from agent.classifier import classify_exception
from agent.comms_checker import check_communications
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.history_checker import check_historical_approval
from agent.memo_generator import generate_memo
from agent.researcher import ResearchResult, research_exception
from agent.rules_engine import (
    RulesDecision,
    gate_communications,
    gate_escalate,
    gate_history,
    gate_research,
    gate_tolerance,
)
from audit.audit_logger import AuditLogger, AuditEvent
from clients.tavily_client import TavilyClient
from config.settings import AppConfig
from models.exception import ExceptionState, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from models.resolution import Resolution, ResolutionAction, RootCause
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


def detect_and_enqueue_exception(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
) -> InvoiceException | None:
    """Classify an invoice triplet and enqueue it if an exception is detected.

    Returns the created InvoiceException (state=RECEIVED) if an exception was
    detected and enqueued, or None for straight-through (clean) invoices.

    This is the pipeline's intake gate: it runs classification, persists the
    exception to the Redis queue, and logs the initial audit event — but does
    not proceed with research or resolution.
    """
    exc = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
    )

    class_res = classify_exception(invoice, po, grn, config, store=store)
    exc.exception_types = class_res.exception_types
    exc.line_variances = class_res.line_variances
    exc.total_variance_usd = class_res.total_variance_usd

    # Straight-through: no exception detected — do not enqueue.
    if not exc.exception_types:
        return None

    store.save(exc)
    audit.log(AuditEvent(
        exception_id=exc.exception_id,
        event_type="classification",
        new_state=ExceptionState.RECEIVED.value,
        details={
            "types": [t.value for t in class_res.exception_types],
            "variance": class_res.total_variance_usd,
            "po_number": po.po_number,
            "invoice_number": invoice.invoice_number,
            "supplier_id": invoice.supplier_id,
            "status": ExceptionState.RECEIVED.value,
        },
    ))

    logger.debug(
        "Enqueued exception %s (types=%s, variance=%.2f)",
        exc.exception_id,
        [t.value for t in exc.exception_types],
        exc.total_variance_usd,
    )
    return exc


class PipelineResult(BaseModel):
    """Full output of a single pipeline run."""

    exception: InvoiceException
    resolution: Resolution
    context: SupplierContext
    research: ResearchResult
    elapsed_seconds: float

    model_config = {"arbitrary_types_allowed": True}


def detect_and_enqueue_exception(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
) -> InvoiceException | None:
    """Classify an invoice/PO/GR triple and enqueue only actual exceptions."""
    classification = classify_exception(invoice, po, grn, config, store=store)
    if not classification.exception_types:
        return None

    exception = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
        exception_types=classification.exception_types,
        line_variances=classification.line_variances,
        total_variance_usd=classification.total_variance_usd,
    )
    store.save(exception)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="classification",
            new_state=ExceptionState.RECEIVED.value,
            details={
                "types": [t.value for t in classification.exception_types],
                "variance": classification.total_variance_usd,
                "po_number": po.po_number,
                "invoice_number": invoice.invoice_number,
                "supplier_id": invoice.supplier_id,
                "status": ExceptionState.RECEIVED.value,
            },
        )
    )
    return exception


def run_pipeline(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    tavily: TavilyClient,
    audit: AuditLogger,
    config: AppConfig,
) -> PipelineResult:
    """Execute the full exception resolution pipeline for one invoice."""
    start_time = time.time()

    # a. Initialize working record
    exception = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
    )

    # b. Classify -> build InvoiceException, persist RECEIVED
    class_res = classify_exception(invoice, po, grn, config, store=store)
    exception.exception_types = class_res.exception_types
    exception.line_variances = class_res.line_variances
    exception.total_variance_usd = class_res.total_variance_usd

    store.save(exception)
    audit.log(AuditEvent(
        exception_id=exception.exception_id,
        event_type="classification",
        details={"types": [t.value for t in class_res.exception_types], "variance": class_res.total_variance_usd}
    ))
    # Short-circuit if straight-through (no exceptions)
    if not exception.exception_types:
        context = retrieve_supplier_context(invoice.supplier_id, store)
        research = ResearchResult(
            queries_run=[],
            findings=[],
            relevance_summary="Skipped research for straight-through invoice.",
            supports_informal_modification=False,
            supporting_evidence=[],
        )
        decision = RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
            confidence=1.0,
            reasoning="Straight-through processing: no variances detected.",
            auto_resolvable=True,
        )

        memo = generate_memo(exception, decision, research, context)
        res = Resolution(exception_id=exception.exception_id, memo=memo, final_state=ExceptionState.RESOLVED)
        store.save_resolution(res)
        audit.log_resolution(res)

        return PipelineResult(
            exception=exception,
            resolution=res,
            context=context,
            research=research,
            elapsed_seconds=time.time() - start_time
        )

    # c. Retrieve supplier context from Redis
    context = retrieve_supplier_context(invoice.supplier_id, store)
    audit.log(AuditEvent(
        exception_id=exception.exception_id,
        event_type="context_retrieved",
        details={"supplier_id": invoice.supplier_id}
    ))

    # d. Transition to TRIAGED and evaluate the gates in order.
    store.transition(exception.exception_id, ExceptionState.TRIAGED)
    audit.log_transition(exception.exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)

    research = ResearchResult(
        queries_run=[],
        findings=[],
        relevance_summary="Research step not needed.",
        supports_informal_modification=False,
        supporting_evidence=[],
    )
    decision = gate_tolerance(exception, config)

    if decision is None:
        history_result = check_historical_approval(exception)
        if history_result.auto_approve:
            decision, _ = gate_history(exception)

    if decision is None:
        comms_result = check_communications(exception)
        if comms_result.auto_approve:
            decision, _ = gate_communications(exception)

    if decision is None:
        store.transition(exception.exception_id, ExceptionState.RESEARCHING)
        audit.log_transition(exception.exception_id, ExceptionState.TRIAGED, ExceptionState.RESEARCHING)

        research = research_exception(exception, context, tavily)
        audit.log(AuditEvent(
            exception_id=exception.exception_id,
            event_type="research_complete",
            details={"queries": research.queries_run}
        ))
        decision = gate_research(exception, research) or gate_escalate()

    audit.log(AuditEvent(
        exception_id=exception.exception_id,
        event_type="rules_applied",
        details={"decision": decision.action.value, "root_cause": decision.root_cause.value}
    ))

    # f. Generate ResolutionMemo
    memo = generate_memo(exception, decision, research, context)
    audit.log(AuditEvent(
        exception_id=exception.exception_id,
        event_type="memo_generated"
    ))

    # g. Transition to RESOLVED or ESCALATED, persist Resolution
    final_state = ExceptionState.RESOLVED if decision.auto_resolvable else ExceptionState.ESCALATED
    store.transition(exception.exception_id, final_state)
    audit.log_transition(exception.exception_id, ExceptionState.RESEARCHING, final_state)

    resolution = Resolution(
        exception_id=exception.exception_id,
        memo=memo,
        final_state=final_state
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)

    return PipelineResult(
        exception=exception,
        resolution=resolution,
        context=context,
        research=research,
        elapsed_seconds=time.time() - start_time
    )
