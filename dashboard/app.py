"""Single-page Streamlit dashboard for invoice exception operations."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import sys
from typing import Callable

import pandas as pd
import streamlit as st

# Ensure project root is importable when Streamlit launches from dashboard/ context.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit.audit_logger import AuditLogger
from audit.audit_logger import AuditEvent
from agent.classifier import classify_exception
from agent.context_retriever import SupplierContext, retrieve_supplier_context
from agent.memo_generator import generate_memo
from agent.researcher import ResearchResult, research_exception
from agent.rules_engine import RulesDecision, apply_rules
from clients.tavily_client import TavilyClient
from clients.redis_client import RedisStreamsClient, get_redis_connection
from config.settings import get_settings
from ingestion.erp_simulator import (
    generate_expedited_shipping_exception,
    generate_informal_modification_exception,
    generate_missing_receipt_exception,
    generate_price_variance_exception,
    generate_quantity_variance_exception,
    generate_straight_through_invoice,
)
from ingestion.json_ingestor import load_dataset
from models.exception import ExceptionState, InvoiceException
from models.grn import GoodsReceiptNote
from models.invoice import Invoice, LineItem
from models.purchase_order import PurchaseOrder
from models.resolution import Resolution, ResolutionAction, RootCause
from reports.spend_variance import SpendVarianceReport, generate_spend_variance_report
from state.redis_backend import RedisStateStore

st.set_page_config(
    page_title="AP Exception Agent",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)


EXCEPTION_TYPE_LABELS = {
    "price_variance": "Price Variance",
    "quantity_variance": "Quantity Variance",
    "missing_goods_receipt": "Missing Goods Receipt",
    "duplicate_invoice": "Duplicate Invoice",
    "informal_modification": "Suspected Informal Modification",
    "none": "No Exception",
}

STATUS_BADGES = {
    "received": "🔴 New",
    "triaged": "🔴 New",
    "researching": "🟡 Researching",
    "pending_approval": "🟡 Pending Approval",
    "resolved": "🟢 Resolved",
    "escalated": "🟠 Escalated",
}


@st.cache_resource
def _init_store() -> RedisStateStore:
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    return RedisStateStore(r)


@st.cache_resource
def _init_audit() -> AuditLogger:
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")
    return AuditLogger(streams)


@st.cache_resource
def _init_tavily() -> TavilyClient:
    config = get_settings()
    return TavilyClient(config.tavily_api_key)


@st.cache_resource
def _load_dataset_triples() -> list[tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None, object]]:
    bundle = load_dataset()
    return bundle.exception_triples()


def _exception_type_label(type_value: str) -> str:
    if type_value in EXCEPTION_TYPE_LABELS:
        return EXCEPTION_TYPE_LABELS[type_value]
    return type_value.replace("_", " ").title()


def _status_badge(state_value: str) -> str:
    return STATUS_BADGES.get(state_value, state_value.replace("_", " ").title())


def _format_duration(delta: timedelta) -> str:
    total_seconds = int(max(delta.total_seconds(), 0))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _safe_datetime_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_currency(value: float) -> str:
    return f"${value:,.2f}"


def _variance_pct(exc: InvoiceException) -> float:
    po_total = exc.purchase_order.total_amount
    if po_total <= 0:
        return 0.0
    return (abs(exc.total_variance_usd) / po_total) * 100


def _collect_exceptions(store: RedisStateStore) -> list[InvoiceException]:
    ids: set[str] = set(store.list_queue_ids())
    for state in ExceptionState:
        ids.update(store.list_by_state(state))

    exceptions: list[InvoiceException] = []
    for exception_id in ids:
        try:
            exceptions.append(store.load(exception_id))
        except KeyError:
            continue
    exceptions.sort(key=lambda e: e.created_at, reverse=True)
    return exceptions


def _collect_resolutions(
    store: RedisStateStore,
    exceptions: list[InvoiceException],
) -> dict[str, Resolution]:
    resolutions: dict[str, Resolution] = {}
    for exc in exceptions:
        res = store.get_resolution(exc.exception_id)
        if res is not None:
            resolutions[exc.exception_id] = res
    return resolutions


def _infer_category(exc: InvoiceException) -> str:
    descriptions = " ".join(li.description.lower() for li in exc.invoice.line_items)
    skus = [li.sku for li in exc.invoice.line_items]

    keyword_map = {
        "paper": "Paper",
        "cardstock": "Paper",
        "steel": "Metals",
        "glove": "Medical Supplies",
        "medical": "Medical Supplies",
        "helmet": "Safety Equipment",
        "ppe": "Safety Equipment",
    }
    for keyword, category in keyword_map.items():
        if keyword in descriptions:
            return category

    if skus:
        prefix = skus[0].split("-")[0]
        prefix_map = {
            "AP": "Paper",
            "SC": "Metals",
            "MS": "Medical Supplies",
            "SG": "Safety Equipment",
        }
        return prefix_map.get(prefix, "Other")

    return "Unknown"


def _selected_rows_from_event(event: object) -> list[int]:
    if event is None:
        return []
    selection = getattr(event, "selection", None)
    if selection is not None:
        rows = getattr(selection, "rows", None)
        if isinstance(rows, list):
            return rows
        if isinstance(selection, dict):
            maybe_rows = selection.get("rows", [])
            if isinstance(maybe_rows, list):
                return maybe_rows
    if isinstance(event, dict):
        maybe_rows = event.get("selection", {}).get("rows", [])
        if isinstance(maybe_rows, list):
            return maybe_rows
    return []


def _build_po_invoice_comparison(exc: InvoiceException) -> pd.DataFrame:
    rows: list[dict] = []
    all_skus = {li.sku for li in exc.purchase_order.line_items} | {
        li.sku for li in exc.invoice.line_items
    }
    for sku in sorted(all_skus):
        po_line = exc.purchase_order.line_item_by_sku(sku)
        inv_line = exc.invoice.line_item_by_sku(sku)
        po_qty = po_line.quantity if po_line else 0
        inv_qty = inv_line.quantity if inv_line else 0
        po_price = po_line.unit_price if po_line else 0.0
        inv_price = inv_line.unit_price if inv_line else 0.0
        po_total = po_line.total if po_line else 0.0
        inv_total = inv_line.total if inv_line else 0.0
        qty_delta = inv_qty - po_qty
        price_delta_pct = ((inv_price - po_price) / po_price * 100) if po_price else 0.0
        rows.append(
            {
                "SKU": sku,
                "Description": (inv_line or po_line).description if (inv_line or po_line) else "",
                "PO Qty": po_qty,
                "Invoice Qty": inv_qty,
                "Qty Delta": qty_delta,
                "PO Unit Price": po_price,
                "Invoice Unit Price": inv_price,
                "Price Delta %": price_delta_pct,
                "PO Line Total": po_total,
                "Invoice Line Total": inv_total,
                "Line Variance": inv_total - po_total,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["PO Unit Price"] = df["PO Unit Price"].map(_safe_currency)
    df["Invoice Unit Price"] = df["Invoice Unit Price"].map(_safe_currency)
    df["Price Delta %"] = df["Price Delta %"].map(lambda x: f"{x:.2f}%")
    df["PO Line Total"] = df["PO Line Total"].map(_safe_currency)
    df["Invoice Line Total"] = df["Invoice Line Total"].map(_safe_currency)
    df["Line Variance"] = df["Line Variance"].map(_safe_currency)
    return df


def _classification_rationale(exc: InvoiceException) -> list[str]:
    reasons: list[str] = []

    if not exc.exception_types:
        reasons.append("No exception types classified (straight-through invoice).")
        return reasons

    type_labels = [_exception_type_label(t.value) for t in exc.exception_types]
    reasons.append(f"Classified as: {', '.join(type_labels)}.")

    if exc.grn is None:
        reasons.append("No goods receipt note was found for the linked PO.")

    for variance in exc.line_variances:
        if variance.is_new_sku:
            reasons.append(f"Invoice contains SKU {variance.sku} that does not exist on the PO.")
        if variance.quantity_delta:
            reasons.append(
                f"Quantity variance on SKU {variance.sku}: delta {variance.quantity_delta}."
            )
        if variance.price_delta_pct is not None and abs(variance.price_delta_pct) > 0:
            reasons.append(
                f"Price variance on SKU {variance.sku}: {variance.price_delta_pct * 100:.2f}%."
            )
        if variance.is_expedited_shipping:
            reasons.append(f"Expedited shipping charge detected on SKU {variance.sku}.")

    return reasons


def _manual_documents(
    supplier_id: str,
    supplier_name: str,
    po_number: str,
    invoice_number: str,
    sku: str,
    description: str,
    product_grade: str,
    po_qty: int,
    po_unit_price: float,
    invoice_qty: int,
    invoice_unit_price: float,
    include_grn: bool,
) -> tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None]:
    today = date.today()
    po_total = round(po_qty * po_unit_price, 2)
    invoice_total = round(invoice_qty * invoice_unit_price, 2)

    po_line = LineItem(
        sku=sku,
        description=description,
        product_grade=product_grade,
        unit_price=po_unit_price,
        quantity=po_qty,
        total=po_total,
    )
    inv_line = LineItem(
        sku=sku,
        description=description,
        product_grade=product_grade,
        unit_price=invoice_unit_price,
        quantity=invoice_qty,
        total=invoice_total,
    )

    po = PurchaseOrder(
        po_number=po_number,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=[po_line],
        total_amount=po_total,
        creation_date=today - timedelta(days=7),
        created_by="manual.demo@company.com",
        department="Procurement",
        cost_center="CC-DEMO",
    )
    invoice = Invoice(
        invoice_number=invoice_number,
        po_number=po_number,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=[inv_line],
        total_amount=invoice_total,
        invoice_date=today,
        due_date=today + timedelta(days=30),
        payment_terms="Net 30",
    )

    grn = None
    if include_grn:
        grn = GoodsReceiptNote(
            gr_number=f"GR-MAN-{today.strftime('%Y%m%d')}",
            po_number=po_number,
            invoice_number=invoice_number,
            supplier_id=supplier_id,
            line_items=[inv_line],
            date_received=today,
            received_by="warehouse.demo@company.com",
            notes="Manual demo GRN",
        )

    return invoice, po, grn


def _transition_and_log(
    store: RedisStateStore,
    audit: AuditLogger,
    exception_id: str,
    to_state: ExceptionState,
) -> InvoiceException:
    current = store.load(exception_id)
    updated = store.transition(exception_id, to_state)
    audit.log_transition(exception_id, current.state, to_state)
    return updated


def _execute_demo_flow(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    store: RedisStateStore,
    audit: AuditLogger,
    tavily: TavilyClient,
    emit: Callable[[str], None] | None = None,
) -> dict:
    config = get_settings()
    stage_rows: list[dict] = []

    def stage_log(stage: str, detail: str, started_at: float) -> None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        stage_rows.append(
            {
                "Stage": stage,
                "Detail": detail,
                "Elapsed (ms)": round(elapsed_ms, 1),
            }
        )

    t0 = time.perf_counter()
    if emit:
        emit("1/6 Detection + Classification started")
    class_res = classify_exception(invoice, po, grn, config, store=store)
    exception = InvoiceException(
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=ExceptionState.RECEIVED,
        exception_types=class_res.exception_types,
        line_variances=class_res.line_variances,
        total_variance_usd=class_res.total_variance_usd,
    )
    store.save(exception)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="classification",
            previous_state=None,
            new_state=exception.state.value,
            details={
                "exception_types": [t.value for t in class_res.exception_types],
                "variance_amount": exception.total_variance_usd,
            },
        )
    )
    stage_log(
        "Detection + Classification",
        (
            ", ".join(_exception_type_label(t.value) for t in class_res.exception_types)
            if class_res.exception_types
            else "No exception type (straight-through)"
        ),
        t0,
    )

    t1 = time.perf_counter()
    if emit:
        emit("2/6 Historical context lookup")
    context = retrieve_supplier_context(invoice.supplier_id, store)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="context_retrieved",
            details={"supplier_id": invoice.supplier_id},
        )
    )
    stage_log(
        "Historical Lookup",
        f"{len(context.historical_exceptions)} historical exception(s)",
        t1,
    )

    t2 = time.perf_counter()
    if emit:
        emit("3/6 External research")
    exception = _transition_and_log(store, audit, exception.exception_id, ExceptionState.TRIAGED)
    if class_res.exception_types:
        exception = _transition_and_log(
            store, audit, exception.exception_id, ExceptionState.RESEARCHING
        )
        research = research_exception(exception, context, tavily)
        audit.log(
            AuditEvent(
                exception_id=exception.exception_id,
                event_type="research_complete",
                details={"queries": research.queries_run},
            )
        )
        stage_log(
            "External Research",
            f"{len(research.queries_run)} query(ies), {len(research.findings)} finding(s)",
            t2,
        )
    else:
        research = ResearchResult(
            queries_run=[],
            findings=[],
            relevance_summary="Skipped research for straight-through invoice.",
            supports_informal_modification=False,
            supporting_evidence=[],
        )
        stage_log("External Research", "Skipped (no exception types)", t2)

    t3 = time.perf_counter()
    if emit:
        emit("4/6 Resolution decision")
    decision = apply_rules(exception, context, research, config)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="rules_applied",
            details={
                "action": decision.action.value,
                "root_cause": decision.root_cause.value,
                "confidence": decision.confidence,
            },
        )
    )
    stage_log(
        "Resolution Decision",
        f"{decision.action.value} ({decision.root_cause.value})",
        t3,
    )

    t4 = time.perf_counter()
    if emit:
        emit("5/6 Memo generation")
    memo = generate_memo(exception, decision, research, context)
    audit.log(
        AuditEvent(
            exception_id=exception.exception_id,
            event_type="memo_generated",
            details={"confidence": memo.confidence},
        )
    )
    stage_log(
        "Memo Generation",
        f"{len(memo.evidence)} evidence item(s)",
        t4,
    )

    t5 = time.perf_counter()
    if emit:
        emit("6/6 State update + persistence")
    if decision.auto_resolvable:
        if exception.state in (ExceptionState.TRIAGED, ExceptionState.RESEARCHING):
            exception = _transition_and_log(
                store, audit, exception.exception_id, ExceptionState.PENDING_APPROVAL
            )
        exception = _transition_and_log(
            store, audit, exception.exception_id, ExceptionState.RESOLVED
        )
        final_state = ExceptionState.RESOLVED
    else:
        exception = _transition_and_log(
            store, audit, exception.exception_id, ExceptionState.ESCALATED
        )
        final_state = ExceptionState.ESCALATED

    resolution = Resolution(
        exception_id=exception.exception_id,
        memo=memo,
        final_state=final_state,
    )
    store.save_resolution(resolution)
    audit.log_resolution(resolution)
    stage_log("State Update", final_state.value, t5)

    return {
        "exception": exception,
        "resolution": resolution,
        "context": context,
        "research": research,
        "decision": decision,
        "stages": stage_rows,
    }


def render_demo_trigger(
    store: RedisStateStore,
    audit: AuditLogger,
) -> None:
    st.header("Demo Trigger")
    st.caption(
        "Submit a new invoice from synthetic data or manual input and watch the "
        "pipeline run from detection to memo generation in real time."
    )

    source_mode = st.radio(
        "Input Source",
        ["Synthetic Scenario", "Synthetic Dataset Record", "Manual Entry"],
        horizontal=True,
    )

    selected_docs: tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None] | None = None

    if source_mode == "Synthetic Scenario":
        scenario = st.selectbox(
            "Scenario",
            [
                "Straight Through",
                "Price Variance",
                "Quantity Variance",
                "Suspected Informal Modification",
                "Expedited Shipping",
                "Missing Goods Receipt",
            ],
        )
        supplier_hint = st.text_input(
            "Optional Supplier ID Override",
            value="",
            placeholder="e.g., SUP-001",
        ).strip() or None

        if scenario == "Straight Through":
            selected_docs = generate_straight_through_invoice(supplier_id=supplier_hint)
        elif scenario == "Price Variance":
            selected_docs = generate_price_variance_exception(supplier_id=supplier_hint)
        elif scenario == "Quantity Variance":
            selected_docs = generate_quantity_variance_exception(supplier_id=supplier_hint)
        elif scenario == "Suspected Informal Modification":
            selected_docs = generate_informal_modification_exception(supplier_id=supplier_hint)
        elif scenario == "Expedited Shipping":
            selected_docs = generate_expedited_shipping_exception(supplier_id=supplier_hint)
        elif scenario == "Missing Goods Receipt":
            selected_docs = generate_missing_receipt_exception(supplier_id=supplier_hint)

    elif source_mode == "Synthetic Dataset Record":
        triples = _load_dataset_triples()
        labels = []
        for idx, (inv, _po, _grn, rec) in enumerate(triples):
            rec_type = getattr(rec, "exception_type", "unknown")
            rec_type_val = rec_type.value if hasattr(rec_type, "value") else str(rec_type)
            labels.append(f"{idx + 1}. {inv.invoice_number} | {rec_type_val}")

        if labels:
            idx = st.selectbox(
                "Select Exception Record",
                options=range(len(labels)),
                format_func=lambda i: labels[i],
            )
            inv, po, grn, _rec = triples[idx]
            selected_docs = (inv, po, grn)
        else:
            st.warning("No dataset exception triples available.")

    else:
        c1, c2 = st.columns(2)
        supplier_id = c1.text_input("Supplier ID", value="SUP-MANUAL-001")
        supplier_name = c2.text_input("Supplier Name", value="Manual Demo Supplier")

        c3, c4 = st.columns(2)
        po_number = c3.text_input(
            "PO Number",
            value=f"PO-MAN-{datetime.now().strftime('%H%M%S')}",
        )
        invoice_number = c4.text_input(
            "Invoice Number",
            value=f"INV-MAN-{datetime.now().strftime('%H%M%S')}",
        )

        c5, c6, c7 = st.columns(3)
        sku = c5.text_input("SKU", value="MAN-ITEM-001")
        description = c6.text_input("Description", value="Manual Demo Item")
        product_grade = c7.text_input("Product Grade", value="Standard")

        c8, c9, c10, c11 = st.columns(4)
        po_qty = int(c8.number_input("PO Qty", min_value=1, value=100))
        po_unit_price = float(c9.number_input("PO Unit Price", min_value=0.0, value=42.0))
        invoice_qty = int(c10.number_input("Invoice Qty", min_value=1, value=95))
        invoice_unit_price = float(
            c11.number_input("Invoice Unit Price", min_value=0.0, value=58.0)
        )
        include_grn = st.checkbox("Include Goods Receipt Note", value=True)

        selected_docs = _manual_documents(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            po_number=po_number,
            invoice_number=invoice_number,
            sku=sku,
            description=description,
            product_grade=product_grade,
            po_qty=po_qty,
            po_unit_price=po_unit_price,
            invoice_qty=invoice_qty,
            invoice_unit_price=invoice_unit_price,
            include_grn=include_grn,
        )

    run_clicked = st.button(
        "Run Live Demo Flow",
        type="primary",
        use_container_width=True,
    )
    if run_clicked and selected_docs is not None:
        invoice, po, grn = selected_docs
        tavily = _init_tavily()
        status = st.status("Running demo pipeline...", expanded=True)
        try:
            result = _execute_demo_flow(
                invoice=invoice,
                po=po,
                grn=grn,
                store=store,
                audit=audit,
                tavily=tavily,
                emit=status.write,
            )
            status.update(
                label=(
                    "Demo complete: "
                    f"{result['resolution'].final_state.value} | "
                    f"{result['decision'].action.value}"
                ),
                state="complete",
                expanded=True,
            )
            st.session_state["demo_last_run"] = result
            st.session_state["selected_exception_id"] = result["exception"].exception_id
        except Exception as err:
            status.update(
                label=f"Demo failed: {err}",
                state="error",
                expanded=True,
            )
            st.exception(err)

    last_run = st.session_state.get("demo_last_run")
    if isinstance(last_run, dict):
        st.subheader("Last Demo Run")
        top = st.columns(4)
        top[0].metric("Exception ID", last_run["exception"].exception_id[:8] + "...")
        top[1].metric("Final State", last_run["resolution"].final_state.value)
        top[2].metric("Action", last_run["decision"].action.value)
        top[3].metric("Confidence", f"{last_run['decision'].confidence:.2f}")

        stage_df = pd.DataFrame(last_run["stages"])
        st.dataframe(stage_df, hide_index=True, use_container_width=True)


def load_exception_queue(store: RedisStateStore) -> pd.DataFrame:
    exceptions = _collect_exceptions(store)
    resolutions = _collect_resolutions(store, exceptions)
    now_utc = datetime.now(timezone.utc)

    rows: list[dict] = []
    for exc in exceptions:
        primary_type = exc.exception_types[0].value if exc.exception_types else "none"
        created = _safe_datetime_utc(exc.created_at)
        resolution = resolutions.get(exc.exception_id)
        if resolution is not None:
            queue_duration = _safe_datetime_utc(resolution.resolved_at) - created
        else:
            queue_duration = now_utc - created

        rows.append(
            {
                "Exception ID": exc.exception_id,
                "Supplier": exc.invoice.supplier_name,
                "Supplier ID": exc.invoice.supplier_id,
                "PO Number": exc.purchase_order.po_number,
                "Invoice Number": exc.invoice.invoice_number,
                "Exception Type": _exception_type_label(primary_type),
                "Exception Type Raw": primary_type,
                "Variance Amount": round(abs(exc.total_variance_usd), 2),
                "Variance %": round(_variance_pct(exc), 2),
                "Status": _status_badge(exc.state.value),
                "Status Raw": exc.state.value,
                "Time in Queue": _format_duration(queue_duration),
                "Created At": created,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "Exception ID",
                "Supplier",
                "PO Number",
                "Invoice Number",
                "Exception Type",
                "Variance Amount",
                "Status",
                "Time in Queue",
            ]
        )

    return pd.DataFrame(rows).sort_values("Created At", ascending=False).reset_index(drop=True)


def _render_analytics_summary(
    exceptions: list[InvoiceException],
    resolutions: dict[str, Resolution],
) -> None:
    st.header("Analytics Summary")

    total_processed = len(exceptions)
    total_variance_usd = sum(abs(exc.total_variance_usd) for exc in exceptions)

    auto_approved = 0
    resolution_hours: list[float] = []
    root_cause_variance: dict[str, float] = {}

    for exc in exceptions:
        resolution = resolutions.get(exc.exception_id)
        if resolution is None:
            continue
        if resolution.memo.action == ResolutionAction.AUTO_APPROVE:
            auto_approved += 1

        duration = _safe_datetime_utc(resolution.resolved_at) - _safe_datetime_utc(exc.created_at)
        resolution_hours.append(duration.total_seconds() / 3600)

        key = resolution.memo.root_cause.value
        root_cause_variance[key] = root_cause_variance.get(key, 0.0) + abs(exc.total_variance_usd)

    auto_resolution_rate = (
        (auto_approved / total_processed) * 100 if total_processed else 0.0
    )
    avg_resolution_hours = (
        sum(resolution_hours) / len(resolution_hours) if resolution_hours else 0.0
    )
    undocumented_mod_variance = root_cause_variance.get(
        RootCause.UNDOCUMENTED_MODIFICATION.value, 0.0
    )

    cols = st.columns(5)
    cols[0].metric("Total Exceptions Processed", f"{total_processed}")
    cols[1].metric("Auto-Resolution Rate", f"{auto_resolution_rate:.1f}%")
    cols[2].metric("Avg Resolution Time", f"{avg_resolution_hours:.1f} hrs")
    cols[3].metric("Total Variance Identified", _safe_currency(total_variance_usd))
    cols[4].metric(
        "Undocumented Modification Variance",
        _safe_currency(undocumented_mod_variance),
    )

    breakdown_rows = [
        {
            "Root Cause": key.replace("_", " ").title(),
            "Variance Amount": value,
        }
        for key, value in root_cause_variance.items()
    ]
    if breakdown_rows:
        breakdown_df = pd.DataFrame(breakdown_rows).sort_values(
            "Variance Amount", ascending=False
        )
        breakdown_df["Variance Amount"] = breakdown_df["Variance Amount"].map(_safe_currency)
        st.caption("Variance Breakdown by Root Cause")
        st.dataframe(breakdown_df, hide_index=True, use_container_width=True)
    else:
        st.caption("No resolved exceptions yet, so root-cause breakdown is empty.")


def render_exception_queue(store: RedisStateStore) -> str | None:
    st.header("Exception Queue View")
    queue_df = load_exception_queue(store)
    if queue_df.empty:
        st.info("No exceptions found in Redis queue/state indexes.")
        return None

    type_options = sorted(queue_df["Exception Type"].dropna().unique().tolist())
    if "Suspected Informal Modification" in type_options:
        type_options = ["Suspected Informal Modification"] + [
            t for t in type_options if t != "Suspected Informal Modification"
        ]

    status_options = sorted(queue_df["Status"].dropna().unique().tolist())
    supplier_options = sorted(queue_df["Supplier"].dropna().unique().tolist())

    only_informal = st.checkbox(
        "Only Suspected Informal Modification",
        value=False,
        help="Quick filter for the key demo exception type.",
    )

    c1, c2, c3, c4 = st.columns(4)
    selected_types = c1.multiselect(
        "Exception Type Filter",
        options=type_options,
        default=type_options,
    )
    selected_statuses = c2.multiselect(
        "Status Filter",
        options=status_options,
        default=status_options,
    )
    selected_suppliers = c3.multiselect(
        "Supplier Filter",
        options=supplier_options,
        default=supplier_options,
    )

    # If user clears a filter entirely, treat it as "all" rather than "none".
    if not selected_types:
        selected_types = type_options
    if not selected_statuses:
        selected_statuses = status_options
    if not selected_suppliers:
        selected_suppliers = supplier_options

    min_variance = float(queue_df["Variance Amount"].min())
    max_variance = float(queue_df["Variance Amount"].max())
    variance_range = c4.slider(
        "Variance Amount Range (USD)",
        min_value=min_variance,
        max_value=max_variance if max_variance > min_variance else min_variance + 1.0,
        value=(min_variance, max_variance if max_variance > min_variance else min_variance + 1.0),
    )

    filtered = queue_df.copy()
    if only_informal:
        filtered = filtered[filtered["Exception Type"] == "Suspected Informal Modification"]
    filtered = filtered[filtered["Exception Type"].isin(selected_types)]
    filtered = filtered[filtered["Status"].isin(selected_statuses)]
    filtered = filtered[filtered["Supplier"].isin(selected_suppliers)]
    filtered = filtered[
        (filtered["Variance Amount"] >= variance_range[0])
        & (filtered["Variance Amount"] <= variance_range[1])
    ]

    if filtered.empty:
        st.warning("No exceptions match the current filter selection.")
        return None

    display = filtered[
        [
            "Exception ID",
            "Supplier",
            "PO Number",
            "Invoice Number",
            "Exception Type",
            "Variance Amount",
            "Status",
            "Time in Queue",
        ]
    ].copy()
    display["Variance Amount"] = display["Variance Amount"].map(_safe_currency)

    event = None
    try:
        event = st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="exception_queue_table",
        )
    except Exception:
        st.dataframe(display, use_container_width=True, hide_index=True)

    selected_id: str | None = None
    selected_rows = _selected_rows_from_event(event)
    if selected_rows:
        row_idx = selected_rows[0]
        if 0 <= row_idx < len(filtered):
            selected_id = filtered.iloc[row_idx]["Exception ID"]

    options = filtered["Exception ID"].tolist()
    if options:
        default_idx = 0
        current_selected = st.session_state.get("selected_exception_id")
        if current_selected in options:
            default_idx = options.index(current_selected)
        selected_from_input = st.selectbox(
            "Open detail panel for exception",
            options=options,
            index=default_idx,
            key="selected_exception_input",
        )
        if selected_id is None:
            selected_id = selected_from_input

    if selected_id:
        st.session_state["selected_exception_id"] = selected_id
    return selected_id


def render_resolution_detail(
    exception_id: str | None,
    store: RedisStateStore,
    audit: AuditLogger,
) -> None:
    st.header("Exception Detail View")
    if not exception_id:
        st.info("Select an exception from the queue to open its detail panel.")
        return

    try:
        exc = store.load(exception_id)
    except KeyError:
        st.error(f"Exception {exception_id} was not found in Redis.")
        return

    resolution = store.get_resolution(exception_id)
    audit_trail = audit.get_exception_trail(exception_id)
    supplier_summary = store.get_supplier_pattern_summary(exc.invoice.supplier_id)

    top_cols = st.columns(4)
    top_cols[0].metric("Exception ID", exc.exception_id[:8] + "...")
    top_cols[1].metric("Supplier", exc.invoice.supplier_name)
    top_cols[2].metric("Variance", _safe_currency(abs(exc.total_variance_usd)))
    top_cols[3].metric("Current Status", _status_badge(exc.state.value))

    tabs = st.tabs(
        [
            "PO vs Invoice Comparison",
            "Classification Rationale",
            "Research Findings",
            "Historical Pattern Data",
            "Resolution Memo",
        ]
    )

    with tabs[0]:
        comparison_df = _build_po_invoice_comparison(exc)
        if comparison_df.empty:
            st.info("No line-item comparison data is available.")
        else:
            st.dataframe(comparison_df, hide_index=True, use_container_width=True)

    with tabs[1]:
        st.markdown("**Detected Exception Types**")
        if exc.exception_types:
            for exc_type in exc.exception_types:
                st.write(f"- {_exception_type_label(exc_type.value)}")
        else:
            st.write("- No exception types detected.")

        st.markdown("**Rationale**")
        for reason in _classification_rationale(exc):
            st.write(f"- {reason}")

    with tabs[2]:
        query_rows: list[str] = []
        for event in audit_trail:
            if event.event_type != "research_complete":
                continue
            queries = event.details.get("queries", [])
            if isinstance(queries, list):
                query_rows.extend(str(q) for q in queries)

        if query_rows:
            st.markdown("**Research Queries Executed**")
            for query in query_rows:
                st.code(query)
        else:
            st.info("No recorded research queries for this exception yet.")

        evidence_rows: list[dict] = []
        if resolution is not None:
            for item in resolution.memo.evidence:
                evidence_rows.append(
                    {
                        "Source": item.source,
                        "Description": item.description,
                        "URL": item.url or "",
                        "Confidence": f"{item.confidence:.2f}",
                    }
                )

        if evidence_rows:
            st.markdown("**Evidence with Source Links**")
            st.dataframe(pd.DataFrame(evidence_rows), hide_index=True, use_container_width=True)
            for row in evidence_rows:
                if row["URL"]:
                    st.markdown(f"- [{row['Description']}]({row['URL']})")
        else:
            st.info("No external research evidence is attached yet.")

    with tabs[3]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Supplier Exceptions", supplier_summary.get("total_exceptions", 0))
        c2.metric("Resolved", supplier_summary.get("resolved_count", 0))
        c3.metric(
            "Informal Modification Cases",
            supplier_summary.get("informal_modification_count", 0),
        )
        avg_uplift = supplier_summary.get("avg_price_uplift_pct")
        c4.metric(
            "Avg Price Uplift",
            f"{(avg_uplift * 100):.2f}%"
            if isinstance(avg_uplift, (int, float))
            else "N/A",
        )

    with tabs[4]:
        if resolution is None:
            st.info("No resolution memo exists yet for this exception.")
        else:
            st.markdown(f"**Root Cause:** {resolution.memo.root_cause.value}")
            st.markdown(f"**Action:** {resolution.memo.action.value}")
            st.markdown(f"**Confidence:** {resolution.memo.confidence:.2f}")
            st.markdown("**Summary**")
            st.write(resolution.memo.summary)


def _build_undocumented_variance_timeseries(
    store: RedisStateStore,
    period_start: date,
    period_end: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    for exception_id in store.list_by_state(ExceptionState.RESOLVED):
        resolution = store.get_resolution(exception_id)
        if resolution is None:
            continue
        if resolution.memo.root_cause != RootCause.UNDOCUMENTED_MODIFICATION:
            continue

        resolved_date = _safe_datetime_utc(resolution.resolved_at).date()
        if resolved_date < period_start or resolved_date > period_end:
            continue

        try:
            exc = store.load(exception_id)
        except KeyError:
            continue

        rows.append(
            {
                "date": resolved_date,
                "supplier": exc.invoice.supplier_name,
                "category": _infer_category(exc),
                "variance": float(abs(exc.total_variance_usd)),
            }
        )

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    ts = pd.DataFrame(rows)
    supplier_daily = (
        ts.groupby(["date", "supplier"], as_index=False)["variance"].sum()
        .pivot(index="date", columns="supplier", values="variance")
        .fillna(0.0)
        .sort_index()
        .cumsum()
    )
    category_daily = (
        ts.groupby(["date", "category"], as_index=False)["variance"].sum()
        .pivot(index="date", columns="category", values="variance")
        .fillna(0.0)
        .sort_index()
        .cumsum()
    )
    return supplier_daily, category_daily


def render_spend_variance(store: RedisStateStore) -> None:
    st.header("Spend Variance Report")
    today = date.today()
    default_start = today - timedelta(days=180)
    c1, c2 = st.columns(2)
    period_start = c1.date_input("Period Start", value=default_start)
    period_end = c2.date_input("Period End", value=today)

    if period_start > period_end:
        st.error("Period start must be on or before period end.")
        return

    report: SpendVarianceReport = generate_spend_variance_report(
        store=store,
        period_start=period_start,
        period_end=period_end,
    )

    summary_cols = st.columns(4)
    summary_cols[0].metric("Documented Spend", f"${report.total_documented_spend:,.2f}")
    summary_cols[1].metric("Actual Spend", f"${report.total_actual_spend:,.2f}")
    summary_cols[2].metric("Total Variance", f"${report.total_variance:,.2f}")
    variance_pct = (
        float(report.total_variance / report.total_documented_spend) * 100
        if report.total_documented_spend > 0
        else 0.0
    )
    summary_cols[3].metric("Variance %", f"{variance_pct:.2f}%")

    if not report.line_items:
        st.info("No undocumented modification resolutions in the selected period.")
        return

    rows = [
        {
            "Supplier": line.supplier_name,
            "Supplier ID": line.supplier_id,
            "Period": line.period,
            "Category": line.category,
            "Documented Spend": float(line.documented_spend),
            "Actual Spend": float(line.actual_spend),
            "Variance (USD)": float(line.variance),
            "Variance %": line.variance_pct * 100,
            "Cases": line.undocumented_modification_count,
        }
        for line in report.line_items
    ]
    line_items_df = pd.DataFrame(rows)

    st.subheader("Variance by Supplier")
    supplier_bar = (
        line_items_df.groupby("Supplier", as_index=False)["Variance (USD)"]
        .sum()
        .sort_values("Variance (USD)", ascending=False)
        .set_index("Supplier")
    )
    st.bar_chart(supplier_bar)

    st.subheader("Cumulative Undocumented Modification Variance Over Time")
    supplier_ts, category_ts = _build_undocumented_variance_timeseries(
        store=store,
        period_start=period_start,
        period_end=period_end,
    )
    if supplier_ts.empty:
        st.info("No timeseries points found for the selected period.")
    else:
        st.caption("By Supplier")
        st.line_chart(supplier_ts)
        st.caption("By Product Category")
        st.line_chart(category_ts)

    st.subheader("Spend Variance Detail")
    display_df = line_items_df.copy()
    display_df["Documented Spend"] = display_df["Documented Spend"].map(_safe_currency)
    display_df["Actual Spend"] = display_df["Actual Spend"].map(_safe_currency)
    display_df["Variance (USD)"] = display_df["Variance (USD)"].map(_safe_currency)
    display_df["Variance %"] = display_df["Variance %"].map(lambda x: f"{x:.2f}%")
    st.dataframe(display_df, hide_index=True, use_container_width=True)

    csv_buffer = StringIO()
    line_items_df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download Spend Variance CSV",
        data=csv_buffer.getvalue(),
        file_name=f"spend_variance_{period_start}_{period_end}.csv",
        mime="text/csv",
    )


def main() -> None:
    store = _init_store()
    audit = _init_audit()

    st.title("Autonomous Invoice Exception Resolution Dashboard")
    st.caption(
        "Queue triage, deep exception context, and spend variance analytics "
        "for AP exception handling."
    )

    render_demo_trigger(store, audit)

    st.markdown("---")
    exceptions = _collect_exceptions(store)
    resolutions = _collect_resolutions(store, exceptions)
    _render_analytics_summary(exceptions, resolutions)

    st.markdown("---")
    selected_id = render_exception_queue(store)
    if selected_id:
        st.session_state["selected_exception_id"] = selected_id

    st.markdown("---")
    render_resolution_detail(
        exception_id=st.session_state.get("selected_exception_id"),
        store=store,
        audit=audit,
    )

    st.markdown("---")
    render_spend_variance(store)


if __name__ == "__main__":
    main()
