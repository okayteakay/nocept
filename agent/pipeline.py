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
from agent.rules_engine import apply_rules, RulesDecision
from audit.audit_logger import AuditLogger, AuditEvent
from clients.tavily_client import TavilyClient
from config.settings import AppConfig
from models.exception import ExceptionState, ExceptionType, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from models.resolution import Resolution, ResolutionAction, RootCause
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
        decision = RulesDecision(
            action=ResolutionAction.AUTO_APPROVE,
            root_cause="policy_compliant_variance", # Needs to be RootCause enum if strictly typed, simplified here
            confidence=1.0,
            reasoning="Straight-through processing: no variances detected.",
            auto_resolvable=True,
        )
        # We need a mock research/context for the result object
        context = retrieve_supplier_context(invoice.supplier_id, store)
        research = ResearchResult(queries_run=[], findings=[], relevance_summary="N/A", supports_informal_modification=False, supporting_evidence=[])
        memo = generate_memo(exception, decision, research, context)
        res = Resolution(exception_id=exception.exception_id, memo=memo, final_state=ExceptionState.RESOLVED)
        store.save_resolution(res)

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

    # d. Transition to RESEARCHING, run Tavily search
    store.transition(exception.exception_id, ExceptionState.RESEARCHING)
    audit.log_transition(exception.exception_id, ExceptionState.RECEIVED, ExceptionState.RESEARCHING)

    research = research_exception(exception, context, tavily)
    audit.log(AuditEvent(
        exception_id=exception.exception_id,
        event_type="research_complete",
        details={"queries": research.queries_run}
    ))

    # e. Apply business rules -> RulesDecision
    decision = apply_rules(exception, context, research, config)
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
