"""
orchestrate/api.py

FastAPI application exposing the six watsonx Orchestrate tools as REST endpoints.

Six-step approval flow
----------------------
Tool 1  /tools/intake               Detect exception; classify type + variance
Tool 2  /tools/tolerance/{id}       Auto-approve if invoice-vs-PO variance <= 1%
Tool 3  /tools/history/{id}         Auto-approve if similar historical case found
Tool 4  /tools/communications/{id}  Auto-approve if email/transcript confirms it
Tool 5  /tools/research/{id}        Auto-approve if web search corroborates it
Tool 6  /tools/resolve/{id}         Finalize: RESOLVED or ESCALATED
"""
from __future__ import annotations

import logging
from collections import deque
from contextlib import asynccontextmanager
from typing import Annotated

import redis as redis_lib
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.classifier import classify_exception
from agent.comms_checker import check_communications
from agent.context_retriever import retrieve_supplier_context
from agent.history_checker import check_historical_approval
from agent.researcher import ResearchResult, research_exception
from agent.rules_engine import (
    RulesDecision,
    gate_communications,
    gate_duplicate,
    gate_escalate,
    gate_history,
    gate_research,
    gate_tolerance,
)
from audit.audit_logger import AuditEvent, AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import AppConfig, get_settings
from ingestion.json_ingestor import DatasetBundle, load_dataset
from knowledge.client import KnowledgeBaseClient
from knowledge.seeder import seed_knowledge_base
from models.exception import ExceptionState, InvoiceException
from models.resolution import EvidenceItem, Resolution, ResolutionAction, ResolutionMemo, RootCause
from state.machine import VALID_TRANSITIONS
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)

_EXC_PFX = "exc:"
_DEC_PFX = "decision:"
_RES_PFX = "research:"
_MEMO_PFX = "memo:"
_TTL = 3600

_res: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    _res.update(
        {
            "cfg": cfg,
            "r": r,
            "store": RedisStateStore(r),
            "tavily": TavilyClient(cfg.tavily_api_key),
            "audit": AuditLogger(streams),
            "dataset": dataset,
            "kb": kb,
        }
    )
    logger.info(
        "Tavily API key loaded: %s",
        f"{cfg.tavily_api_key[:12]}..." if cfg.tavily_api_key else "NOT SET",
    )
    logger.info("Orchestrate API ready — dataset loaded, Redis connected, KB seeded.")
    yield
    _res.clear()


app = FastAPI(
    title="Invoice Exception Resolution — Agent Tools",
    description=(
        "Six tools powering the autonomous invoice exception resolution agent. "
        "Import /openapi.json into IBM watsonx Orchestrate to register all tools at once."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


def _get_store() -> RedisStateStore:
    return _res["store"]


def _get_r() -> redis_lib.Redis:
    return _res["r"]


def _get_tavily() -> TavilyClient:
    return _res["tavily"]


def _get_audit() -> AuditLogger:
    return _res["audit"]


def _get_cfg() -> AppConfig:
    return _res["cfg"]


def _get_dataset() -> DatasetBundle:
    return _res["dataset"]


def _get_kb() -> KnowledgeBaseClient:
    return _res["kb"]


Store = Annotated[RedisStateStore, Depends(_get_store)]
R = Annotated[redis_lib.Redis, Depends(_get_r)]
Tavily = Annotated[TavilyClient, Depends(_get_tavily)]
Audit = Annotated[AuditLogger, Depends(_get_audit)]
Cfg = Annotated[AppConfig, Depends(_get_cfg)]
DS = Annotated[DatasetBundle, Depends(_get_dataset)]
KB = Annotated[KnowledgeBaseClient, Depends(_get_kb)]


def _rset(r: redis_lib.Redis, prefix: str, eid: str, data: str) -> None:
    r.set(f"{prefix}{eid}", data, ex=_TTL)


def _load_exc(r: redis_lib.Redis, eid: str, store: RedisStateStore | None = None) -> InvoiceException:
    # Prefer the store's copy — it is updated on every state transition.
    # Fall back to the raw exc: cache only if the store copy is unavailable.
    if store is not None:
        try:
            return store.load(eid)
        except KeyError:
            pass
    raw = r.get(f"{_EXC_PFX}{eid}")
    if raw is None:
        raise HTTPException(404, f"Exception '{eid}' not found. Call Tool 1 first.")
    payload = raw if isinstance(raw, str) else raw.decode()
    return InvoiceException.model_validate_json(payload)


class IntakeRequest(BaseModel):
    invoice_number: str = Field(description="Invoice number, e.g. 'INV-0001'")
    po_number: str = Field(description="Purchase order number, e.g. 'PO-0001'")
    grn_number: str | None = Field(
        default=None,
        description="Optional GRN number. Leave null to resolve by PO number from the dataset.",
    )


class IntakeResponse(BaseModel):
    exception_id: str
    exception_types: list[str]
    total_variance_usd: float
    informal_modification_signals: list[str]
    is_straight_through: bool
    message: str


class ToleranceResponse(BaseModel):
    exception_id: str
    auto_approved: bool
    variance_within_tolerance: bool
    price_tolerance_pct: float
    reasoning: str
    message: str


class HistoryResponse(BaseModel):
    exception_id: str
    auto_approved: bool
    candidates_checked: int
    best_match_id: str | None
    variance_gap_pct: float | None
    reasoning: str
    message: str


class CommsResponse(BaseModel):
    exception_id: str
    auto_approved: bool
    communications_checked: int
    best_source_id: str | None
    best_confidence: float | None
    reasoning: str
    message: str


class ResearchResponse(BaseModel):
    exception_id: str
    auto_approved: bool
    queries_run: list[str]
    findings_count: int
    supports_informal_modification: bool
    relevance_summary: str
    message: str


class ResolveRequest(BaseModel):
    notes: str | None = Field(default=None)


class ResolveResponse(BaseModel):
    exception_id: str
    final_state: str
    action_taken: str
    approved_by_step: int
    message: str


@app.post(
    "/tools/intake",
    response_model=IntakeResponse,
    tags=["Tools"],
    summary="Tool 1 — Exception Intake",
)
async def intake(
    req: IntakeRequest,
    store: Store,
    r: R,
    cfg: Cfg,
    audit: Audit,
    dataset: DS,
) -> IntakeResponse:
    invoice = dataset.invoices.get(req.invoice_number)
    if invoice is None:
        raise HTTPException(404, f"Invoice '{req.invoice_number}' not found.")

    po = dataset.purchase_orders.get(req.po_number)
    if po is None:
        raise HTTPException(404, f"PO '{req.po_number}' not found.")

    grn = dataset.goods_receipts.get(req.po_number)

    exc = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
    )

    exc_record = dataset.exception_for_invoice(req.invoice_number)
    if exc_record is not None:
        exc.exception_record = exc_record
        exc.related_emails = dataset.emails_for_exception(exc_record)
        exc.related_transcripts = dataset.transcripts_for_exception(exc_record)

    classification = classify_exception(invoice, po, grn, cfg, store=store)
    exc.exception_types = classification.exception_types
    exc.line_variances = classification.line_variances
    exc.total_variance_usd = classification.total_variance_usd

    store.save(exc)
    _rset(r, _EXC_PFX, exc.exception_id, exc.model_dump_json())

    audit.log(
        AuditEvent(
            exception_id=exc.exception_id,
            event_type="classification",
            details={
                "types": [t.value for t in classification.exception_types],
                "variance_usd": classification.total_variance_usd,
                "invoice": req.invoice_number,
                "po": req.po_number,
                "linked_emails": len(exc.related_emails),
                "linked_transcripts": len(exc.related_transcripts),
            },
        )
    )

    is_clean = not bool(classification.exception_types)
    return IntakeResponse(
        exception_id=exc.exception_id,
        exception_types=[t.value for t in classification.exception_types],
        total_variance_usd=classification.total_variance_usd,
        informal_modification_signals=classification.informal_modification_signals,
        is_straight_through=is_clean,
        message=(
            "No exceptions detected — straight-through invoice. Call Tool 6 to resolve."
            if is_clean
            else "Exception detected. Call Tool 2 (tolerance) next."
        ),
    )


@app.get(
    "/tools/tolerance/{exception_id}",
    response_model=ToleranceResponse,
    tags=["Tools"],
    summary="Tool 2 — Tolerance Check",
)
async def tolerance(
    exception_id: str,
    r: R,
    store: Store,
    cfg: Cfg,
    audit: Audit,
) -> ToleranceResponse:
    exc = _load_exc(r, exception_id, store)

    if exc.state == ExceptionState.RECEIVED:
        store.transition(exception_id, ExceptionState.TRIAGED)
        audit.log_transition(exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)
        exc = _load_exc(r, exception_id, store)

    dup_decision = gate_duplicate(exc)
    if dup_decision:
        _rset(r, _DEC_PFX, exception_id, dup_decision.model_dump_json())
        audit.log(
            AuditEvent(
                exception_id=exception_id,
                event_type="gate_fired",
                details={"gate": "duplicate", "action": dup_decision.action.value},
            )
        )
        return ToleranceResponse(
            exception_id=exception_id,
            auto_approved=True,
            variance_within_tolerance=False,
            price_tolerance_pct=cfg.price_tolerance_pct,
            reasoning=dup_decision.reasoning,
            message="Duplicate invoice detected. Call Tool 6 to reject.",
        )

    decision = gate_tolerance(exc, cfg)
    if decision:
        _rset(r, _DEC_PFX, exception_id, decision.model_dump_json())
        audit.log(
            AuditEvent(
                exception_id=exception_id,
                event_type="gate_fired",
                details={"gate": "tolerance", "action": decision.action.value},
            )
        )

    return ToleranceResponse(
        exception_id=exception_id,
        auto_approved=decision is not None,
        variance_within_tolerance=decision is not None,
        price_tolerance_pct=cfg.price_tolerance_pct,
        reasoning=(
            decision.reasoning
            if decision
            else (
                f"Absolute invoice-to-PO variance exceeds the configured "
                f"{cfg.price_tolerance_pct:.0%} tolerance. Proceed to historical check."
            )
        ),
        message=(
            "Variance within tolerance — auto-approved. Call Tool 6 to resolve."
            if decision
            else "Variance exceeds tolerance. Call Tool 3 (history) next."
        ),
    )


@app.get(
    "/tools/history/{exception_id}",
    response_model=HistoryResponse,
    tags=["Tools"],
    summary="Tool 3 — Historical Approval Check",
)
async def history(
    exception_id: str,
    r: R,
    store: Store,
    audit: Audit,
) -> HistoryResponse:
    exc = _load_exc(r, exception_id, store)

    if exc.state == ExceptionState.RECEIVED:
        store.transition(exception_id, ExceptionState.TRIAGED)
        audit.log_transition(exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)

    result = check_historical_approval(exc)
    if result.auto_approve:
        decision, _ = gate_history(exc)
        if decision:
            _rset(r, _DEC_PFX, exception_id, decision.model_dump_json())
            audit.log(
                AuditEvent(
                    exception_id=exception_id,
                    event_type="gate_fired",
                    details={
                        "gate": "history",
                        "match": result.best_match.exception_id if result.best_match else None,
                    },
                )
            )

    best = result.best_match
    return HistoryResponse(
        exception_id=exception_id,
        auto_approved=result.auto_approve,
        candidates_checked=result.candidates_checked,
        best_match_id=best.exception_id if best else None,
        variance_gap_pct=best.variance_diff if best else None,
        reasoning=result.reasoning,
        message=(
            "Similar historical approved case found — auto-approved. Call Tool 6 to resolve."
            if result.auto_approve
            else "No sufficient historical match found. Call Tool 4 (communications) next."
        ),
    )


@app.get(
    "/tools/communications/{exception_id}",
    response_model=CommsResponse,
    tags=["Tools"],
    summary="Tool 4 — Communications Confirmation",
)
async def communications(
    exception_id: str,
    r: R,
    store: Store,
    audit: Audit,
) -> CommsResponse:
    exc = _load_exc(r, exception_id, store)

    result = check_communications(exc)
    if result.auto_approve:
        decision, _ = gate_communications(exc)
        if decision:
            _rset(r, _DEC_PFX, exception_id, decision.model_dump_json())
            audit.log(
                AuditEvent(
                    exception_id=exception_id,
                    event_type="gate_fired",
                    details={
                        "gate": "communications",
                        "source": (
                            result.best_confirmation.source_id
                            if result.best_confirmation
                            else None
                        ),
                    },
                )
            )

    best = result.best_confirmation
    return CommsResponse(
        exception_id=exception_id,
        auto_approved=result.auto_approve,
        communications_checked=result.total_checked,
        best_source_id=best.source_id if best else None,
        best_confidence=best.confidence if best else None,
        reasoning=result.reasoning,
        message=(
            "Communications directly confirm this exception — auto-approved. Call Tool 6 to resolve."
            if result.auto_approve
            else "Communications do not sufficiently confirm the exception. Call Tool 5 (research) next."
        ),
    )


@app.post(
    "/tools/research/{exception_id}",
    response_model=ResearchResponse,
    tags=["Tools"],
    summary="Tool 5 — Web Research",
)
async def research(
    exception_id: str,
    r: R,
    store: Store,
    tavily: Tavily,
    audit: Audit,
) -> ResearchResponse:
    exc = _load_exc(r, exception_id, store)

    if exc.state == ExceptionState.TRIAGED:
        store.transition(exception_id, ExceptionState.RESEARCHING)
        audit.log_transition(exception_id, ExceptionState.TRIAGED, ExceptionState.RESEARCHING)
    elif exc.state == ExceptionState.RECEIVED:
        store.transition(exception_id, ExceptionState.TRIAGED)
        audit.log_transition(exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)
        store.transition(exception_id, ExceptionState.RESEARCHING)
        audit.log_transition(exception_id, ExceptionState.TRIAGED, ExceptionState.RESEARCHING)

    context = retrieve_supplier_context(exc.invoice.supplier_id, store)
    result = research_exception(exc, context, tavily)
    _rset(r, _RES_PFX, exception_id, result.model_dump_json())

    decision = gate_research(exc, result)
    if decision:
        _rset(r, _DEC_PFX, exception_id, decision.model_dump_json())
        audit.log(
            AuditEvent(
                exception_id=exception_id,
                event_type="gate_fired",
                details={"gate": "research", "findings": len(result.findings)},
            )
        )
    else:
        existing = r.get(f"{_DEC_PFX}{exception_id}")
        if existing is None:
            _rset(r, _DEC_PFX, exception_id, gate_escalate().model_dump_json())

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="research_complete",
            details={
                "queries": result.queries_run,
                "findings": len(result.findings),
                "corroborates": result.supports_informal_modification,
            },
        )
    )

    return ResearchResponse(
        exception_id=exception_id,
        auto_approved=decision is not None,
        queries_run=result.queries_run,
        findings_count=len(result.findings),
        supports_informal_modification=result.supports_informal_modification,
        relevance_summary=result.relevance_summary,
        message=(
            "Web research corroborates this exception — auto-approved. Call Tool 6 to resolve."
            if decision
            else "No strong external corroboration found. Call Tool 6 to escalate to human review."
        ),
    )


@app.post(
    "/tools/resolve/{exception_id}",
    response_model=ResolveResponse,
    tags=["Tools"],
    summary="Tool 6 — Resolve / Escalate",
)
async def resolve(
    exception_id: str,
    store: Store,
    r: R,
    audit: Audit,
    kb: KB,
    req: ResolveRequest | None = None,
) -> ResolveResponse:
    exc = _load_exc(r, exception_id, store)

    dec_raw = r.get(f"{_DEC_PFX}{exception_id}")
    if dec_raw is None:
        decision = _straight_through_decision()
    else:
        dec_str = dec_raw if isinstance(dec_raw, str) else dec_raw.decode()
        decision = RulesDecision.model_validate_json(dec_str)

    final_state = (
        ExceptionState.RESOLVED if decision.auto_resolvable else ExceptionState.ESCALATED
    )

    evidence: list[EvidenceItem] = []
    res_raw = r.get(f"{_RES_PFX}{exception_id}")
    research_result: ResearchResult | None = None
    if res_raw is not None:
        res_str = res_raw if isinstance(res_raw, str) else res_raw.decode()
        research_result = ResearchResult.model_validate_json(res_str)
        evidence.extend(research_result.supporting_evidence)

    resolution_memo = ResolutionMemo(
        exception_id=exception_id,
        root_cause=decision.root_cause,
        action=decision.action,
        confidence=decision.confidence,
        summary=_build_summary(exc, decision),
        evidence=evidence,
    )
    _rset(r, _MEMO_PFX, exception_id, resolution_memo.model_dump_json())

    _walk_to_final(store, audit, exception_id, exc.state, final_state)

    resolution = Resolution(
        exception_id=exception_id,
        memo=resolution_memo,
        final_state=final_state,
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)

    notes = req.notes if req else None
    if notes:
        audit.log(
            AuditEvent(
                exception_id=exception_id,
                event_type="human_note",
                actor="human",
                details={"notes": notes},
            )
        )

    exc_final = store.load(exception_id)
    kb.ingest_resolved_case(exc_final, resolution)

    return ResolveResponse(
        exception_id=exception_id,
        final_state=final_state.value,
        action_taken=decision.action.value,
        approved_by_step=decision.approved_by_step,
        message=(
            f"Exception {exception_id} {final_state.value.lower()}. "
            f"Action: {decision.action.value}. "
            f"Approved by step: {decision.approved_by_step}."
            + (f" Note: {notes}" if notes else "")
        ),
    )


def _straight_through_decision() -> RulesDecision:
    return RulesDecision(
        action=ResolutionAction.AUTO_APPROVE,
        root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
        confidence=1.0,
        reasoning="Straight-through invoice — three-way match passed with no variances.",
        auto_resolvable=True,
        approved_by_step=1,
    )


def _build_summary(exc: InvoiceException, decision: RulesDecision) -> str:
    types_str = ", ".join(t.value for t in exc.exception_types) or "none"
    return (
        f"Invoice {exc.invoice.invoice_number} vs PO {exc.purchase_order.po_number} "
        f"(Supplier: {exc.supplier_name}). "
        f"Exception type(s): {types_str}. "
        f"Variance: ${exc.total_variance_usd:,.2f}. "
        f"Decision: {decision.action.value} (confidence {decision.confidence:.0%}). "
        f"{decision.reasoning}"
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
    """
    if current == target or current in (ExceptionState.RESOLVED, ExceptionState.ESCALATED):
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
    summary="Tool 7 — Semantic Email Search",
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
    summary="Tool 8 — Semantic Transcript Search",
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
