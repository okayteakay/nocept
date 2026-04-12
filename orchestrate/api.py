"""
orchestrate/api.py

FastAPI application exposing the six watsonx Orchestrate tools as REST endpoints.
Each tool wraps one pipeline module. An exception_id (returned by Tool 1 / /tools/intake)
is the shared key passed between tools — watsonx Orchestrate threads it through the conversation.

Intermediate state (supplier context, research result, decision, memo) is stored as
JSON strings in Redis with a 1-hour TTL between tool calls.

--- How to wire up in watsonx Orchestrate ---
1. Run the server:  uvicorn orchestrate.api:app --port 8000
2. Expose it publicly (e.g. ngrok: `ngrok http 8000`)
3. In watsonx Orchestrate > Tools > Import > OpenAPI:
   paste the public URL + /openapi.json
4. All six endpoints appear as importable tools.
5. Create an agent, add all six tools, paste the system prompt from orchestrate/agent_prompt.md.
"""
from __future__ import annotations

import json
import logging
import textwrap
from contextlib import asynccontextmanager
from typing import Annotated

import redis as redis_lib
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.classifier import classify_exception
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.memo_generator import generate_memo
from agent.researcher import ResearchResult, research_exception
from agent.rules_engine import RulesDecision, apply_rules
from audit.audit_logger import AuditEvent, AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import AppConfig, get_settings
from ingestion.json_ingestor import DatasetBundle, load_dataset
from knowledge.client import KnowledgeBaseClient
from knowledge.seeder import seed_knowledge_base
from models.exception import ExceptionState, InvoiceException
from models.resolution import Resolution
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)

# Intermediate-state key prefixes (stored in Redis, 1-hour TTL)
_CTX_PFX = "ctx:"       # SupplierContext JSON
_RES_PFX = "research:"  # ResearchResult JSON
_DEC_PFX = "decision:"  # RulesDecision JSON
_MEMO_PFX = "memo:"     # ResolutionMemo JSON

_TTL = 3600  # seconds

# ---------------------------------------------------------------------------
# Shared resource lifecycle
# ---------------------------------------------------------------------------

_res: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    cfg.configure_logging()
    r = get_redis_connection(cfg.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")
    dataset = load_dataset()

    # Initialise knowledge base and seed from dataset (upsert-safe)
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
    logger.info("Orchestrate API ready — dataset loaded, Redis connected, KB seeded.")
    yield
    _res.clear()


app = FastAPI(
    title="Invoice Exception Resolution — Agent Tools",
    description=(
        "Six tools powering the autonomous invoice exception resolution agent. "
        "Import /openapi.json into IBM watsonx Orchestrate to register all tools at once."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# FastAPI dependency helpers
# ---------------------------------------------------------------------------


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

# ---------------------------------------------------------------------------
# Redis helpers for intermediate pipeline state
# ---------------------------------------------------------------------------


def _redis_set(r: redis_lib.Redis, prefix: str, eid: str, data: str) -> None:
    r.set(f"{prefix}{eid}", data, ex=_TTL)


def _redis_get(r: redis_lib.Redis, prefix: str, eid: str, label: str) -> str:
    raw = r.get(f"{prefix}{eid}")
    if raw is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{label} not found for exception '{eid}'. "
                "Ensure the preceding tool has been called first."
            ),
        )
    return raw if isinstance(raw, str) else raw.decode()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class IntakeRequest(BaseModel):
    invoice_number: str = Field(
        description="Invoice number from the dataset, e.g. 'INV-2024-00123'"
    )
    po_number: str = Field(
        description="Purchase Order number this invoice references, e.g. 'PO-2024-00456'"
    )
    grn_number: str | None = Field(
        default=None,
        description=(
            "Goods Receipt Note number, if known. "
            "Leave null to auto-resolve from the dataset by PO number."
        ),
    )


class IntakeResponse(BaseModel):
    exception_id: str = Field(
        description="Unique ID for this exception — pass this to all subsequent tool calls."
    )
    exception_types: list[str] = Field(
        description="Detected exception type(s), e.g. ['INFORMAL_MODIFICATION', 'PRICE_VARIANCE']"
    )
    total_variance_usd: float = Field(
        description="Absolute USD variance between invoice total and PO total."
    )
    informal_modification_signals: list[str] = Field(
        description="Signals explaining why an informal modification is suspected (empty if none)."
    )
    is_straight_through: bool = Field(
        description=(
            "True if no exceptions detected. "
            "If True, skip to Tool 6 to mark as resolved — no research needed."
        )
    )
    message: str


class HistoryResponse(BaseModel):
    exception_id: str
    supplier_id: str
    total_historical_exceptions: int = Field(
        description="Number of exception records on file for this supplier."
    )
    substitution_patterns: list[dict] = Field(
        description=(
            "Known SKU substitution patterns observed for this supplier: "
            "[{from_sku, to_sku, count, avg_price_uplift_pct}]"
        )
    )
    average_price_uplift_pct: float | None = Field(
        description="Average historical price uplift from informal modifications, or null if no history."
    )
    exception_rate: float | None = Field(
        description="Fraction of this supplier's invoices that generated exceptions."
    )
    pattern_confidence: float = Field(
        description=(
            "0.0–1.0 — how strongly the current exception matches a known historical pattern. "
            "Above 0.8 means a repeat pattern; below 0.3 means novel."
        )
    )
    message: str


class ResearchResponse(BaseModel):
    exception_id: str
    queries_run: list[str] = Field(description="Exact search queries submitted to Tavily.")
    findings_count: int = Field(description="Number of unique search results returned.")
    supports_informal_modification: bool = Field(
        description="True if at least one finding corroborates a substitution or undocumented change."
    )
    relevance_summary: str = Field(
        description="Plain-English summary of what external research found."
    )
    message: str


class DecideResponse(BaseModel):
    exception_id: str
    action: str = Field(
        description="Recommended action: AUTO_APPROVE, AUTO_REJECT, or ESCALATE_TO_HUMAN."
    )
    root_cause: str = Field(description="Detected root cause category.")
    confidence: float = Field(description="0.0–1.0 decision confidence.")
    reasoning: str = Field(description="Plain-English explanation of which rule fired and why.")
    auto_resolvable: bool = Field(
        description=(
            "True → call Tool 5 then Tool 6 to auto-resolve. "
            "False → call Tool 5 then Tool 6, which will escalate to a human."
        )
    )
    message: str


class MemoResponse(BaseModel):
    exception_id: str
    summary: str = Field(description="Human-readable exception summary.")
    root_cause: str
    action: str
    confidence: float
    evidence_count: int = Field(description="Number of evidence items cited.")
    full_memo_json: str = Field(
        description="Complete ResolutionMemo as JSON — store this for the audit trail."
    )


class ResolveRequest(BaseModel):
    notes: str | None = Field(
        default=None,
        description="Optional human override notes to append to the audit trail.",
    )


class ResolveResponse(BaseModel):
    exception_id: str
    final_state: str = Field(description="Final exception state: RESOLVED or ESCALATED.")
    action_taken: str
    message: str


# ---------------------------------------------------------------------------
# Tool 1 — Exception Intake
# ---------------------------------------------------------------------------


@app.post(
    "/tools/intake",
    response_model=IntakeResponse,
    tags=["Tools"],
    summary="Tool 1 — Exception Intake",
    description=(
        "Receive an invoice reference, run three-way matching against the PO and GRN, "
        "classify all exception types, and persist the exception to Redis. "
        "Returns an exception_id that must be passed to all subsequent tool calls. "
        "If is_straight_through is True, skip directly to Tool 6."
    ),
)
async def intake(
    req: IntakeRequest,
    store: Store,
    cfg: Cfg,
    audit: Audit,
    dataset: DS,
) -> IntakeResponse:
    invoice = dataset.invoices.get(req.invoice_number)
    if invoice is None:
        raise HTTPException(404, f"Invoice '{req.invoice_number}' not found in dataset.")

    po = dataset.purchase_orders.get(req.po_number)
    if po is None:
        raise HTTPException(404, f"PO '{req.po_number}' not found in dataset.")

    # Auto-resolve GRN from dataset by PO number unless explicitly provided
    grn = dataset.goods_receipts.get(req.po_number)

    exc = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
    )

    classification = classify_exception(invoice, po, grn, cfg, store=store)
    exc.exception_types = classification.exception_types
    exc.line_variances = classification.line_variances
    exc.total_variance_usd = classification.total_variance_usd
    store.save(exc)

    audit.log(
        AuditEvent(
            exception_id=exc.exception_id,
            event_type="classification",
            details={
                "types": [t.value for t in classification.exception_types],
                "variance_usd": classification.total_variance_usd,
                "invoice": req.invoice_number,
                "po": req.po_number,
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
            else (
                f"Exception(s) detected: {', '.join(t.value for t in classification.exception_types)}. "
                f"Variance: ${classification.total_variance_usd:,.2f}. "
                "Call Tool 2 (history) next."
            )
        ),
    )


# ---------------------------------------------------------------------------
# Tool 2 — Historical Pattern Lookup
# ---------------------------------------------------------------------------


@app.get(
    "/tools/history/{exception_id}",
    response_model=HistoryResponse,
    tags=["Tools"],
    summary="Tool 2 — Historical Pattern Lookup",
    description=(
        "Query Redis for all resolved exceptions from the same supplier and product category. "
        "Compute substitution pattern statistics and a confidence score. "
        "Stores the supplier context in Redis for Tools 3 and 4."
    ),
)
async def history(
    exception_id: str,
    store: Store,
    r: R,
    audit: Audit,
) -> HistoryResponse:
    try:
        exc = store.load(exception_id)
    except KeyError:
        raise HTTPException(404, f"Exception '{exception_id}' not found. Call Tool 1 first.")

    context = retrieve_supplier_context(exc.invoice.supplier_id, store)
    _redis_set(r, _CTX_PFX, exception_id, context.model_dump_json())

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="context_retrieved",
            details={
                "supplier_id": exc.invoice.supplier_id,
                "history_count": len(context.historical_exceptions),
                "patterns_found": len(context.substitution_patterns),
            },
        )
    )

    # Compute pattern confidence: does a known pattern match the current exception's SKU swap?
    new_skus = {v.sku for v in exc.line_variances if v.is_new_sku}
    shortfall_skus = {
        v.sku
        for v in exc.line_variances
        if not v.is_new_sku and v.quantity_delta is not None and v.quantity_delta < 0
    }
    pattern_confidence = 0.0
    for p in context.substitution_patterns:
        if p["from_sku"] in shortfall_skus and p["to_sku"] in new_skus:
            # Scale confidence: 2 occurrences = 0.4, 5+ = 1.0
            pattern_confidence = min(1.0, p["count"] / 5.0)
            break

    return HistoryResponse(
        exception_id=exception_id,
        supplier_id=context.supplier_id,
        total_historical_exceptions=len(context.historical_exceptions),
        substitution_patterns=context.substitution_patterns,
        average_price_uplift_pct=context.average_price_uplift_pct,
        exception_rate=context.exception_rate,
        pattern_confidence=pattern_confidence,
        message=(
            f"Found {len(context.historical_exceptions)} historical exception(s) "
            f"for supplier {context.supplier_id}. "
            f"Pattern match confidence: {pattern_confidence:.0%}. "
            "Call Tool 3 (research) next."
        ),
    )


# ---------------------------------------------------------------------------
# Tool 3 — External Research
# ---------------------------------------------------------------------------


@app.post(
    "/tools/research/{exception_id}",
    response_model=ResearchResponse,
    tags=["Tools"],
    summary="Tool 3 — External Research",
    description=(
        "Construct targeted Tavily search queries based on the exception type and supplier, "
        "score findings for relevance, and store the top results in Redis. "
        "Also transitions the exception state to RESEARCHING. "
        "Requires Tool 2 to have run first."
    ),
)
async def research(
    exception_id: str,
    store: Store,
    r: R,
    tavily: Tavily,
    audit: Audit,
) -> ResearchResponse:
    try:
        exc = store.load(exception_id)
    except KeyError:
        raise HTTPException(404, f"Exception '{exception_id}' not found.")

    ctx_raw = _redis_get(r, _CTX_PFX, exception_id, "Supplier context")
    context = SupplierContext.model_validate_json(ctx_raw)

    # State transitions: RECEIVED → TRIAGED → RESEARCHING
    if exc.state == ExceptionState.RECEIVED:
        store.transition(exception_id, ExceptionState.TRIAGED)
        audit.log_transition(exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)
    store.transition(exception_id, ExceptionState.RESEARCHING)
    audit.log_transition(exception_id, ExceptionState.TRIAGED, ExceptionState.RESEARCHING)

    result = research_exception(exc, context, tavily)
    _redis_set(r, _RES_PFX, exception_id, result.model_dump_json())

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="research_complete",
            details={
                "queries": result.queries_run,
                "findings": len(result.findings),
                "corroborates_modification": result.supports_informal_modification,
            },
        )
    )

    return ResearchResponse(
        exception_id=exception_id,
        queries_run=result.queries_run,
        findings_count=len(result.findings),
        supports_informal_modification=result.supports_informal_modification,
        relevance_summary=result.relevance_summary,
        message=(
            f"Research complete: {len(result.findings)} finding(s) across "
            f"{len(result.queries_run)} search query/queries. "
            + (
                "At least one finding corroborates the modification."
                if result.supports_informal_modification
                else "No strong external corroboration found."
            )
            + " Call Tool 4 (decide) next."
        ),
    )


# ---------------------------------------------------------------------------
# Tool 4 — Resolution Decision
# ---------------------------------------------------------------------------


@app.post(
    "/tools/decide/{exception_id}",
    response_model=DecideResponse,
    tags=["Tools"],
    summary="Tool 4 — Resolution Decision",
    description=(
        "Apply the business rules decision tree using the exception classification, "
        "historical pattern data, and Tavily research findings. "
        "Returns a resolution recommendation with confidence score. "
        "Requires Tools 2 and 3 to have run first."
    ),
)
async def decide(
    exception_id: str,
    store: Store,
    r: R,
    cfg: Cfg,
    audit: Audit,
) -> DecideResponse:
    try:
        exc = store.load(exception_id)
    except KeyError:
        raise HTTPException(404, f"Exception '{exception_id}' not found.")

    ctx_raw = _redis_get(r, _CTX_PFX, exception_id, "Supplier context")
    res_raw = _redis_get(r, _RES_PFX, exception_id, "Research result")

    context = SupplierContext.model_validate_json(ctx_raw)
    research_result = ResearchResult.model_validate_json(res_raw)

    decision = apply_rules(exc, context, research_result, cfg)
    _redis_set(r, _DEC_PFX, exception_id, decision.model_dump_json())

    audit.log(
        AuditEvent(
            exception_id=exception_id,
            event_type="rules_applied",
            details={
                "action": decision.action.value,
                "root_cause": decision.root_cause.value,
                "confidence": decision.confidence,
                "auto_resolvable": decision.auto_resolvable,
            },
        )
    )

    next_step = (
        "Call Tool 5 (memo) then Tool 6 (resolve) to auto-resolve."
        if decision.auto_resolvable
        else "Call Tool 5 (memo) then Tool 6 (resolve) — will escalate to human review."
    )

    return DecideResponse(
        exception_id=exception_id,
        action=decision.action.value,
        root_cause=decision.root_cause.value,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        auto_resolvable=decision.auto_resolvable,
        message=f"Decision: {decision.action.value} ({decision.confidence:.0%} confidence). {next_step}",
    )


# ---------------------------------------------------------------------------
# Tool 5 — Resolution Memo
# ---------------------------------------------------------------------------


@app.get(
    "/tools/memo/{exception_id}",
    response_model=MemoResponse,
    tags=["Tools"],
    summary="Tool 5 — Memo Generation",
    description=(
        "Assemble the structured resolution memo: PO vs invoice comparison, "
        "root cause, evidence with source links, recommended action, and confidence. "
        "Stores the memo in Redis so Tool 6 can attach it to the final Resolution record. "
        "Requires Tools 2, 3, and 4 to have run first."
    ),
)
async def memo(
    exception_id: str,
    store: Store,
    r: R,
    audit: Audit,
) -> MemoResponse:
    try:
        exc = store.load(exception_id)
    except KeyError:
        raise HTTPException(404, f"Exception '{exception_id}' not found.")

    ctx_raw = _redis_get(r, _CTX_PFX, exception_id, "Supplier context")
    res_raw = _redis_get(r, _RES_PFX, exception_id, "Research result")
    dec_raw = _redis_get(r, _DEC_PFX, exception_id, "Decision")

    context = SupplierContext.model_validate_json(ctx_raw)
    research_result = ResearchResult.model_validate_json(res_raw)
    decision = RulesDecision.model_validate_json(dec_raw)

    resolution_memo = generate_memo(exc, decision, research_result, context)
    _redis_set(r, _MEMO_PFX, exception_id, resolution_memo.model_dump_json())

    audit.log(AuditEvent(exception_id=exception_id, event_type="memo_generated"))

    return MemoResponse(
        exception_id=exception_id,
        summary=resolution_memo.summary,
        root_cause=resolution_memo.root_cause.value,
        action=resolution_memo.action.value,
        confidence=resolution_memo.confidence,
        evidence_count=len(resolution_memo.evidence),
        full_memo_json=resolution_memo.model_dump_json(),
    )


# ---------------------------------------------------------------------------
# Tool 6 — State Update & Audit
# ---------------------------------------------------------------------------


@app.post(
    "/tools/resolve/{exception_id}",
    response_model=ResolveResponse,
    tags=["Tools"],
    summary="Tool 6 — State Update & Audit",
    description=(
        "Transition the exception to its final state (RESOLVED or ESCALATED), "
        "persist the Resolution record to Redis, and log the closing audit event. "
        "This must be the last tool called. "
        "Requires Tool 4 (and usually Tools 2, 3, 5) to have run first. "
        "For straight-through invoices (is_straight_through=True from Tool 1), "
        "call this directly after Tool 1 with no notes."
    ),
)
async def resolve(
    exception_id: str,
    req: ResolveRequest,
    store: Store,
    r: R,
    audit: Audit,
    kb: KB,
) -> ResolveResponse:
    try:
        exc = store.load(exception_id)
    except KeyError:
        raise HTTPException(404, f"Exception '{exception_id}' not found.")

    # Load decision (may be absent for straight-through path)
    dec_raw = r.get(f"{_DEC_PFX}{exception_id}")
    memo_raw = r.get(f"{_MEMO_PFX}{exception_id}")

    if dec_raw is None:
        # Straight-through: no exceptions, auto-approve
        from models.resolution import ResolutionAction, RootCause, ResolutionMemo

        resolution_memo = ResolutionMemo(
            exception_id=exception_id,
            root_cause=RootCause.POLICY_COMPLIANT_VARIANCE,
            action=ResolutionAction.AUTO_APPROVE,
            confidence=1.0,
            summary="Straight-through invoice — three-way match passed with no variances detected.",
            evidence=[],
        )
        final_state = ExceptionState.RESOLVED
        action_taken = ResolutionAction.AUTO_APPROVE.value
    else:
        dec_str = dec_raw if isinstance(dec_raw, str) else dec_raw.decode()
        decision = RulesDecision.model_validate_json(dec_str)
        final_state = ExceptionState.RESOLVED if decision.auto_resolvable else ExceptionState.ESCALATED
        action_taken = decision.action.value

        if memo_raw is not None:
            from models.resolution import ResolutionMemo

            memo_str = memo_raw if isinstance(memo_raw, str) else memo_raw.decode()
            resolution_memo = ResolutionMemo.model_validate_json(memo_str)
        else:
            # Memo was skipped — build a minimal one from the decision
            from models.resolution import ResolutionMemo

            resolution_memo = ResolutionMemo(
                exception_id=exception_id,
                root_cause=decision.root_cause,
                action=decision.action,
                confidence=decision.confidence,
                summary=decision.reasoning,
                evidence=[],
            )

    # Transition state machine
    current = exc.state
    valid_pre_resolve = {
        ExceptionState.RECEIVED,
        ExceptionState.TRIAGED,
        ExceptionState.RESEARCHING,
    }
    if current in valid_pre_resolve:
        # For states that don't have a direct path, we need to walk the machine.
        # The state machine allows RESEARCHING → RESOLVED/ESCALATED. For
        # RECEIVED/TRIAGED (short-circuit paths), transition through RESEARCHING first.
        if current == ExceptionState.RECEIVED:
            store.transition(exception_id, ExceptionState.TRIAGED)
            audit.log_transition(exception_id, ExceptionState.RECEIVED, ExceptionState.TRIAGED)
            current = ExceptionState.TRIAGED
        if current == ExceptionState.TRIAGED:
            store.transition(exception_id, ExceptionState.RESEARCHING)
            audit.log_transition(exception_id, ExceptionState.TRIAGED, ExceptionState.RESEARCHING)
            current = ExceptionState.RESEARCHING
        store.transition(exception_id, final_state)
        audit.log_transition(exception_id, current, final_state)
    # If already RESOLVED/ESCALATED, treat as idempotent

    resolution = Resolution(
        exception_id=exception_id,
        memo=resolution_memo,
        final_state=final_state,
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)

    if req.notes:
        audit.log(
            AuditEvent(
                exception_id=exception_id,
                event_type="human_note",
                actor="human",
                details={"notes": req.notes},
            )
        )

    # Ingest the finalized case into the knowledge base so future pipeline runs
    # and human searches can reference it.
    exc_final = store.load(exception_id)
    kb.ingest_resolved_case(exc_final, resolution)

    return ResolveResponse(
        exception_id=exception_id,
        final_state=final_state.value,
        action_taken=action_taken,
        message=(
            f"Exception {exception_id} {final_state.value.lower()}. "
            f"Action: {action_taken}."
            + (f" Note: {req.notes}" if req.notes else "")
        ),
    )


# ---------------------------------------------------------------------------
# Knowledge Base Search — Tool 7 & 8
# ---------------------------------------------------------------------------


class KBEmailSearchRequest(BaseModel):
    query: str = Field(
        description=(
            "Free-text semantic query. May reference buyer/seller names, "
            "PO/invoice numbers, exception types, or any topic in email communications."
        )
    )
    top_k: int = Field(default=10, ge=1, le=50, description="Maximum results to return.")
    date_filter: str | None = Field(
        default=None,
        description="Restrict to a single date — ISO format, e.g. '2026-01-15'.",
    )
    po_filter: str | None = Field(
        default=None,
        description="Restrict to emails referencing an exact PO number, e.g. 'PO-0023'.",
    )
    invoice_filter: str | None = Field(
        default=None,
        description="Restrict to emails referencing an exact invoice number.",
    )


class KBEmailSearchResponse(BaseModel):
    query: str
    results: list[dict] = Field(
        description=(
            "Matched emails ordered by semantic similarity (score 0–1, higher = better). "
            "Each entry has: email_id, subject, sender, receiver, date, "
            "related_po, related_invoice, body, score."
        )
    )
    total: int


class KBTranscriptSearchRequest(BaseModel):
    query: str = Field(
        description=(
            "Free-text semantic query. May reference caller/callee names, organizations, "
            "PO/invoice numbers, or any topic discussed in calls."
        )
    )
    top_k: int = Field(default=10, ge=1, le=50, description="Maximum results to return.")
    date_filter: str | None = Field(
        default=None,
        description="Restrict to a single call date — ISO format, e.g. '2026-01-15'.",
    )
    po_filter: str | None = Field(
        default=None,
        description="Restrict to transcripts referencing an exact PO number.",
    )
    invoice_filter: str | None = Field(
        default=None,
        description="Restrict to transcripts referencing an exact invoice number.",
    )


class KBTranscriptSearchResponse(BaseModel):
    query: str
    results: list[dict] = Field(
        description=(
            "Matched transcripts ordered by semantic similarity (score 0–1, higher = better). "
            "Each entry has: transcript_id, caller, caller_organization, callee, "
            "callee_organization, date, duration_minutes, related_po, related_invoice, "
            "transcript, score."
        )
    )
    total: int


@app.post(
    "/kb/search/emails",
    response_model=KBEmailSearchResponse,
    tags=["Knowledge Base"],
    summary="Tool 7 — Semantic Email Search",
    description=(
        "Semantic (vector) search over all stored email communications. "
        "Finds emails by topic, buyer/seller name, PO number, invoice number, "
        "or any phrase from the subject or body — even if the exact words differ. "
        "Optional exact filters for date, PO, and invoice narrow the results. "
        "Useful for finding evidence before or after an exception is raised."
    ),
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
    description=(
        "Semantic (vector) search over all stored phone call transcripts. "
        "Finds calls by topic, speaker name, organization, PO/invoice reference, "
        "or any phrase from the conversation — handles unstructured call text well. "
        "Optional exact filters for date, PO, and invoice narrow the results."
    ),
)
async def search_transcripts(
    req: KBTranscriptSearchRequest, kb: KB
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
    description=(
        "Return aggregate resolution statistics and the most recent cases for a supplier. "
        "Includes exception type breakdown, average agent confidence, and recent summaries."
    ),
)
async def supplier_history(supplier_id: str, kb: KB) -> dict:
    return kb.resolutions.supplier_summary(supplier_id)
