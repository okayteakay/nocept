"""Streamlit Dashboard — Autonomous Invoice Exception Resolution Agent.

Four pages:
    1. Exception Queue  — live table of all open exceptions with filters
    2. Resolution Detail — invoice vs PO diff, memo, evidence, confidence
    3. Spend Variance    — chart + table of undocumented modification spend
    4. Audit Trail       — Redis Streams event log for a selected exception

Run with::

    streamlit run dashboard/app.py
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from config.settings import get_settings
from models.exception import ExceptionState, ExceptionType, InvoiceException
from models.resolution import Resolution
from reports.spend_variance import SpendVarianceReport, generate_spend_variance_report
from state.redis_backend import RedisStateStore

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AP Exception Agent",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Shared resource initialization (cached)
# ---------------------------------------------------------------------------

@st.cache_resource
def _init_store() -> RedisStateStore:
    """Initialize and cache the Redis state store."""
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    return RedisStateStore(r)


@st.cache_resource
def _init_audit() -> AuditLogger:
    """Initialize and cache the audit logger."""
    config = get_settings()
    r = get_redis_connection(config.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")
    return AuditLogger(streams)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_exception_queue(store: RedisStateStore) -> pd.DataFrame:
    """Load all open exceptions from Redis and return as a DataFrame.

    Columns: exception_id, supplier_name, state, exception_types,
             total_variance_usd, invoice_id, po_number, created_at.

    Args:
        store: The Redis state store.

    Returns:
        DataFrame with one row per exception.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def render_exception_queue(store: RedisStateStore) -> None:
    """Render Page 1: Exception Queue.

    Displays a filterable, sortable table of all exceptions. Sidebar filters:
    - State (multiselect)
    - Exception Type (multiselect)
    - Supplier (multiselect)
    - Variance amount range (slider)

    Clicking a row populates st.session_state["selected_exception_id"] so
    other pages can show detail for it.

    Args:
        store: The Redis state store.
    """
    st.header("Exception Queue")
    raise NotImplementedError


def render_resolution_detail(exception_id: str, store: RedisStateStore) -> None:
    """Render Page 2: Resolution Detail for a single exception.

    Sections:
    - Invoice vs PO comparison table (line-by-line delta highlighting)
    - Resolution memo (root cause, action, confidence gauge)
    - Evidence citations (source, description, URL, confidence)
    - GRN receipt status

    Args:
        exception_id: UUID of the exception to display.
        store: The Redis state store.
    """
    st.header("Resolution Detail")
    raise NotImplementedError


def render_spend_variance(store: RedisStateStore) -> None:
    """Render Page 3: Spend Variance Report.

    Controls:
    - Date range picker (default: last 90 days)
    - Export to CSV button

    Displays:
    - Summary KPIs (total documented, actual, variance, variance %)
    - Bar chart: variance by supplier
    - Detail table: all SpendVarianceLineItems

    Args:
        store: The Redis state store.
    """
    st.header("Spend Variance Report")
    raise NotImplementedError


def render_audit_trail(exception_id: str, audit: AuditLogger) -> None:
    """Render Page 4: Audit Trail for a single exception.

    Displays a chronological timeline of all AuditEvents for the exception,
    formatted as an expandable list with timestamps, event types, and details.

    Args:
        exception_id: UUID of the exception.
        audit: The AuditLogger for fetching stream events.
    """
    st.header("Audit Trail")
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point called by `streamlit run dashboard/app.py`."""
    store = _init_store()
    audit = _init_audit()

    page = st.sidebar.radio(
        "Navigation",
        ["Exception Queue", "Resolution Detail", "Spend Variance", "Audit Trail"],
    )

    if page == "Exception Queue":
        render_exception_queue(store)

    elif page == "Resolution Detail":
        exception_id = st.session_state.get("selected_exception_id", "")
        if not exception_id:
            exception_id = st.text_input("Exception ID", placeholder="Paste exception UUID here")
        if exception_id:
            render_resolution_detail(exception_id, store)
        else:
            st.info("Select an exception from the Exception Queue or enter an ID above.")

    elif page == "Spend Variance":
        render_spend_variance(store)

    elif page == "Audit Trail":
        exception_id = st.session_state.get("selected_exception_id", "")
        if not exception_id:
            exception_id = st.text_input("Exception ID", placeholder="Paste exception UUID here")
        if exception_id:
            render_audit_trail(exception_id, audit)
        else:
            st.info("Select an exception from the Exception Queue or enter an ID above.")


if __name__ == "__main__":
    main()
