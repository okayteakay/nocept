"""Autonomous invoice exception resolution agent using LangGraph.

Replaces the six-step IBM WatsonX Orchestrate flow with an in-process
LangGraph state machine. Each node calls the corresponding business logic
from agent/ module without modification.
"""
from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import StateGraph, END

from agent.classifier import classify_exception
from agent.comms_checker import check_communications
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.history_checker import check_historical_approval
from agent.memo_generator import generate_memo
from agent.rules_engine import (
    RulesDecision,
    gate_communications,
    gate_duplicate,
    gate_escalate,
    gate_history,
    gate_tolerance,
)
from audit.audit_logger import AuditEvent, AuditLogger
from config.settings import AppConfig
from models.exception import ExceptionState, InvoiceException, ExceptionType
from models.resolution import Resolution, ResolutionAction, ResolutionMemo, RootCause
from state.machine import SHORTEST_PATHS
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
    memo: ResolutionMemo | None


def node_classify(
    state: AgentState,
    store: RedisStateStore,
    config: AppConfig,
    audit: AuditLogger,
) -> dict:
    """Node 1: Classify exception. Skip if already classified at intake."""
    exception = state["exception"]

    if exception.exception_types:
        # Already classified at intake — skip re-classification.
        audit.log(
            AuditEvent(
                exception_id=exception.exception_id,
                event_type="classification_unchanged",
                details={
                    "types": [t.value for t in exception.exception_types],
                    "variance_usd": exception.total_variance_usd,
                },
            )
        )
        return {"exception": exception}

    classification = classify_exception(
        exception.invoice,
        exception.purchase_order,
        exception.grn,
        config,
        store=store,
        exception_id=exception.exception_id,
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

    # Return the updated exception so downstream nodes see the new state.
    return {"decision": decision, "exception": exception}


def node_gate_history(
    state: AgentState,
    store: RedisStateStore,
    audit: AuditLogger,
) -> dict:
    """Node 4: Check historical approvals for similar exceptions."""
    exception = state["exception"]

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


def node_generate_memo(
    state: AgentState,
) -> dict:
    """Node 6: Generate resolution memo from all gathered evidence.

    When the pipeline short-circuits (Gate 0 duplicate, Gate 1 straight-through)
    some fields may be None. Use safe defaults so the memo generator can still
    build a useful summary.
    """
    exception = state["exception"]
    decision = state["decision"]
    context = state["context"]

    if context is None:
        context = SupplierContext(
            supplier_id=exception.invoice.supplier_id, historical_exceptions=[],
            substitution_patterns=[], average_price_uplift_pct=None, exception_rate=None,
        )
    if decision is None:
        decision = RulesDecision(
            action=ResolutionAction.ESCALATE_TO_HUMAN,
            root_cause=RootCause.UNRESOLVED, confidence=0.0,
            reasoning="No decision was made by the pipeline; defaulting to escalation.",
            auto_resolvable=False, approved_by_step=0,
        )

    memo = generate_memo(exception, decision, context)
    return {"memo": memo}


def node_persist(
    state: AgentState,
    store: RedisStateStore,
    audit: AuditLogger,
) -> dict:
    """Node 7: Persist resolution to Redis and audit trail."""
    exception = state["exception"]
    decision = state["decision"]
    memo = state["memo"]

    # Safe default if decision is somehow None
    if decision is None:
        decision = RulesDecision(
            action=ResolutionAction.ESCALATE_TO_HUMAN,
            root_cause=RootCause.UNRESOLVED,
            confidence=0.0,
            reasoning="No decision was made; defaulting to escalation.",
            auto_resolvable=False,
            approved_by_step=0,
        )

    final_state = (
        ExceptionState.RESOLVED if decision.auto_resolvable else ExceptionState.ESCALATED
    )

    # Walk state to final — precomputed shortest paths for the reachable combos
    current = store.load(exception.exception_id).state
    for next_state in SHORTEST_PATHS.get((current, final_state), []):
        store.transition(exception.exception_id, next_state)
        audit.log_transition(exception.exception_id, current, next_state)
        current = next_state

    resolution = Resolution(
        exception_id=exception.exception_id,
        memo=memo,
        final_state=final_state,
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)

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
    """Route after comms: straight to memo generation."""
    return "generate_memo"


def build_agent(
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
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

    builder.add_edge("gate_comms", "generate_memo")

    builder.add_edge("generate_memo", "persist")
    builder.add_edge("persist", END)

    # Compile the graph. No in-memory checkpointer: end-to-end latency is
    # <30s, and BackgroundTasks provide durable async execution.
    graph = builder.compile()

    return graph


def run_pipeline(
    exception_id: str,
    store: RedisStateStore,
    audit: AuditLogger,
    config: AppConfig,
) -> Resolution:
    """Execute the full invoice exception resolution pipeline.

    Loads exception from Redis, builds initial state, invokes LangGraph,
    and returns the final resolution.

    Args:
        exception_id: ID of the exception to resolve
        store: RedisStateStore instance
        audit: AuditLogger instance
        config: AppConfig instance

    Returns:
        Resolution object with final state and memo

    Raises:
        KeyError: if exception_id not found in store
        Exception: if pipeline fails (LLM, etc.)
    """
    logger.info(f"Starting pipeline for exception {exception_id}")

    exception = store.load(exception_id)
    logger.info(
        f"Loaded exception {exception_id}: "
        f"{exception.invoice.invoice_number} vs {exception.purchase_order.po_number}"
    )

    graph = build_agent(store, audit, config)

    initial_state: AgentState = {
        "exception_id": exception_id,
        "exception": exception,
        "context": None,
        "decision": None,
        "history_result": None,
        "comms_result": None,
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
