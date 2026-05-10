"""Autonomous invoice exception resolution agent using LangGraph.

Replaces the six-step IBM WatsonX Orchestrate flow with an in-process
LangGraph state machine. Each node calls the corresponding business logic
from agent/ module without modification.
"""
from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agent.classifier import classify_exception
from agent.comms_checker import check_communications
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.history_checker import check_historical_approval
from agent.memo_generator import generate_memo
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
from clients.redis_client import get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import AppConfig
from models.exception import ExceptionState, InvoiceException, ExceptionType
from models.resolution import Resolution, ResolutionMemo
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """State payload passed through all LangGraph nodes."""

    exception_id: str
    exception: InvoiceException
    context: SupplierContext | None
    decision: RulesDecision | None
    history_result: object | None  # HistoricalCheckResult
    comms_result: object | None  # CommsCheckResult
    research_result: ResearchResult | None
    memo: ResolutionMemo | None


def node_classify(
    state: AgentState,
    store: RedisStateStore,
    config: AppConfig,
    audit: AuditLogger,
) -> dict:
    """Node 1: Classify exception by comparing invoice, PO, and GRN."""
    exception = state["exception"]
    classification = classify_exception(
        exception.invoice,
        exception.purchase_order,
        exception.grn,
        config,
        store=store,
    )

    exception.exception_types = classification.exception_types
    exception.line_variances = classification.line_variances
    exception.total_variance_usd = classification.total_variance_usd

    store.save(exception)

    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="classification",
            details={
                "types": [t.value for t in classification.exception_types],
                "variance_usd": classification.total_variance_usd,
                "invoice": exception.invoice.invoice_number,
                "po": exception.purchase_order.po_number,
            },
        )
    )

    return {"exception": exception}


def node_get_context(
    state: AgentState,
    store: RedisStateStore,
) -> dict:
    """Node 2: Retrieve supplier context (history, patterns, etc.)."""
    exception = state["exception"]
    context = retrieve_supplier_context(exception.invoice.supplier_id, store)
    return {"context": context}


def node_gate_tolerance(
    state: AgentState,
    config: AppConfig,
    store: RedisStateStore,
    audit: AuditLogger,
) -> dict:
    """Node 3: Check if variance is within tolerance threshold."""
    exception = state["exception"]

    if exception.state == ExceptionState.RECEIVED:
        store.transition(exception.exception_id, ExceptionState.TRIAGED)
        audit.log_transition(
            exception.exception_id,
            ExceptionState.RECEIVED,
            ExceptionState.TRIAGED,
        )
        exception = store.load(exception.exception_id)

    dup_decision = gate_duplicate(exception)
    if dup_decision:
        audit.log(
            AuditEvent(
                exception_id=exception.exception_id,
                event_type="gate_fired",
                details={"gate": "duplicate", "action": dup_decision.action.value},
            )
        )
        return {"decision": dup_decision}

    decision = gate_tolerance(exception, config)
    if decision:
        audit.log(
            AuditEvent(
                exception_id=exception.exception_id,
                event_type="gate_fired",
                details={"gate": "tolerance", "action": decision.action.value},
            )
        )

    return {"decision": decision}


def node_gate_history(
    state: AgentState,
    store: RedisStateStore,
    audit: AuditLogger,
) -> dict:
    """Node 4: Check historical approvals for similar exceptions."""
    exception = state["exception"]

    if exception.state == ExceptionState.RECEIVED:
        store.transition(exception.exception_id, ExceptionState.TRIAGED)
        audit.log_transition(
            exception.exception_id,
            ExceptionState.RECEIVED,
            ExceptionState.TRIAGED,
        )

    result = check_historical_approval(exception)
    decision = None
    if result.auto_approve:
        decision, _ = gate_history(exception)
        if decision:
            audit.log(
                AuditEvent(
                    exception_id=exception.exception_id,
                    event_type="gate_fired",
                    details={
                        "gate": "history",
                        "match": (
                            result.best_match.exception_id
                            if result.best_match
                            else None
                        ),
                    },
                )
            )

    return {"decision": decision, "history_result": result}


def node_gate_comms(
    state: AgentState,
    audit: AuditLogger,
) -> dict:
    """Node 5: Check communications (emails/transcripts) for confirmation."""
    exception = state["exception"]

    result = check_communications(exception)
    decision = None
    if result.auto_approve:
        decision, _ = gate_communications(exception)
        if decision:
            audit.log(
                AuditEvent(
                    exception_id=exception.exception_id,
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

    return {"decision": decision, "comms_result": result}


def node_research(
    state: AgentState,
    store: RedisStateStore,
    tavily: TavilyClient,
    audit: AuditLogger,
) -> dict:
    """Node 6: Run web research to corroborate exception."""
    exception = state["exception"]
    context = state["context"]

    if exception.state == ExceptionState.TRIAGED:
        store.transition(exception.exception_id, ExceptionState.RESEARCHING)
        audit.log_transition(
            exception.exception_id,
            ExceptionState.TRIAGED,
            ExceptionState.RESEARCHING,
        )
    elif exception.state == ExceptionState.RECEIVED:
        store.transition(exception.exception_id, ExceptionState.TRIAGED)
        audit.log_transition(
            exception.exception_id,
            ExceptionState.RECEIVED,
            ExceptionState.TRIAGED,
        )
        store.transition(exception.exception_id, ExceptionState.RESEARCHING)
        audit.log_transition(
            exception.exception_id,
            ExceptionState.TRIAGED,
            ExceptionState.RESEARCHING,
        )

    research_result = research_exception(exception, context, tavily)

    decision = gate_research(exception, research_result)
    if not decision:
        decision = gate_escalate()

    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="research_complete",
            details={
                "queries": research_result.queries_run,
                "findings": len(research_result.findings),
                "corroborates": research_result.supports_informal_modification,
            },
        )
    )

    return {"decision": decision, "research_result": research_result}


def node_generate_memo(
    state: AgentState,
) -> dict:
    """Node 7: Generate resolution memo from all gathered evidence."""
    exception = state["exception"]
    decision = state["decision"]
    research_result = state["research_result"]
    context = state["context"]

    memo = generate_memo(exception, decision, research_result, context)
    return {"memo": memo}


def node_persist(
    state: AgentState,
    store: RedisStateStore,
    audit: AuditLogger,
) -> dict:
    """Node 8: Persist resolution to Redis and audit trail, send notifications."""
    exception = state["exception"]
    decision = state["decision"]
    memo = state["memo"]

    final_state = (
        ExceptionState.RESOLVED if decision.auto_resolvable else ExceptionState.ESCALATED
    )

    # Walk state machine to final state
    current = exception.state
    if current != final_state and current not in (
        ExceptionState.RESOLVED,
        ExceptionState.ESCALATED,
    ):
        store.transition(exception.exception_id, final_state)
        audit.log_transition(exception.exception_id, current, final_state)

    resolution = Resolution(
        exception_id=exception.exception_id,
        memo=memo,
        final_state=final_state,
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)

    exception = store.load(exception.exception_id)
    from knowledge.client import KnowledgeBaseClient
    from clients.redis_client import get_redis_connection
    from config.settings import get_settings
    from notifications.notifier import Notifier

    cfg = get_settings()
    r = get_redis_connection(cfg.redis_url)
    kb = KnowledgeBaseClient.from_config(r, cfg)
    kb.ingest_resolved_case(exception, resolution)

    # Send notifications
    try:
        notifier = Notifier(cfg)
        if final_state == ExceptionState.ESCALATED:
            notifier.notify_escalation(exception, memo)
        else:
            notifier.notify_resolution(exception, memo, final_state)
    except Exception as e:
        logger.warning(f"Failed to send notification: {e}")

    return {}


def route_after_classify(state: AgentState) -> str:
    """Route after classify: short-circuit if no exception or duplicate."""
    exception = state["exception"]

    if ExceptionType.DUPLICATE_INVOICE in exception.exception_types:
        return "generate_memo"

    if not exception.exception_types:
        return "generate_memo"  # straight-through

    return "get_context"


def route_after_tolerance(state: AgentState) -> str:
    """Route after tolerance: to memo if decided, else to history."""
    return "generate_memo" if state["decision"] else "gate_history"


def route_after_history(state: AgentState) -> str:
    """Route after history: to memo if decided, else to comms."""
    return "generate_memo" if state["decision"] else "gate_comms"


def route_after_comms(state: AgentState) -> str:
    """Route after comms: to memo if decided, else to research."""
    return "generate_memo" if state["decision"] else "research"


def build_agent(
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
    tavily: TavilyClient,
) -> object:
    """Build the compiled LangGraph state machine.

    Returns a runnable CompiledGraph that can be invoked with initial state.
    """
    from functools import partial

    builder = StateGraph(AgentState)

    # Add nodes — inject dependencies via partial
    builder.add_node(
        "classify",
        partial(node_classify, store=store, config=config, audit=audit),
    )
    builder.add_node("get_context", partial(node_get_context, store=store))
    builder.add_node(
        "gate_tolerance",
        partial(
            node_gate_tolerance,
            config=config,
            store=store,
            audit=audit,
        ),
    )
    builder.add_node(
        "gate_history",
        partial(node_gate_history, store=store, audit=audit),
    )
    builder.add_node("gate_comms", partial(node_gate_comms, audit=audit))
    builder.add_node(
        "research",
        partial(
            node_research,
            store=store,
            tavily=tavily,
            audit=audit,
        ),
    )
    builder.add_node("generate_memo", node_generate_memo)
    builder.add_node("persist", partial(node_persist, store=store, audit=audit))

    # Edges
    builder.set_entry_point("classify")

    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "get_context": "get_context",
            "generate_memo": "generate_memo",
        },
    )

    builder.add_edge("get_context", "gate_tolerance")

    builder.add_conditional_edges(
        "gate_tolerance",
        route_after_tolerance,
        {
            "generate_memo": "generate_memo",
            "gate_history": "gate_history",
        },
    )

    builder.add_conditional_edges(
        "gate_history",
        route_after_history,
        {
            "generate_memo": "generate_memo",
            "gate_comms": "gate_comms",
        },
    )

    builder.add_conditional_edges(
        "gate_comms",
        route_after_comms,
        {
            "generate_memo": "generate_memo",
            "research": "research",
        },
    )

    builder.add_edge("research", "generate_memo")
    builder.add_edge("generate_memo", "persist")
    builder.add_edge("persist", END)

    # Compile with in-memory checkpoint for resumability
    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    return graph


def run_pipeline(
    exception_id: str,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
    tavily: TavilyClient,
) -> Resolution:
    """Execute the full invoice exception resolution pipeline.

    Loads exception from Redis, builds initial state, invokes LangGraph,
    and returns the final resolution.

    Args:
        exception_id: ID of the exception to resolve
        store: RedisStateStore instance
        audit: AuditLogger instance
        config: AppConfig instance
        tavily: TavilyClient instance

    Returns:
        Resolution object with final state and memo

    Raises:
        KeyError: if exception_id not found in store
        Exception: if pipeline fails (LLM, Tavily, etc.)
    """
    logger.info(f"Starting pipeline for exception {exception_id}")

    exception = store.load(exception_id)
    logger.info(
        f"Loaded exception {exception_id}: "
        f"{exception.invoice.invoice_number} vs {exception.purchase_order.po_number}"
    )

    graph = build_agent(store, audit, config, tavily)

    initial_state: AgentState = {
        "exception_id": exception_id,
        "exception": exception,
        "context": None,
        "decision": None,
        "history_result": None,
        "comms_result": None,
        "research_result": None,
        "memo": None,
    }

    logger.info(f"Invoking graph for exception {exception_id}")
    final_state = graph.invoke(initial_state)

    resolution = None
    try:
        # Try to load the saved resolution from store
        resolution = store.get_resolution(exception_id)
    except Exception as e:
        logger.error(f"Failed to load resolution: {e}")

    if resolution is None:
        # Fallback: construct from final state
        logger.warning(
            f"Could not load resolution from store; using final state memo"
        )
        resolution = Resolution(
            exception_id=exception_id,
            memo=final_state.get("memo"),
            final_state=(
                ExceptionState.RESOLVED
                if final_state.get("decision", {}).get("auto_resolvable")
                else ExceptionState.ESCALATED
            ),
        )

    logger.info(f"Pipeline complete: {exception_id} → {resolution.final_state.value}")
    return resolution
