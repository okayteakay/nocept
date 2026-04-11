from __future__ import annotations

import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Invoice Exception Webhook Receiver",
    description="Receives invoice, PO, and GRN events from ERP systems and routes them into the resolution pipeline.",
    version="0.1.0",
)


class WebhookPayload(BaseModel):
    """Inbound event payload from an ERP system or integration layer."""

    event_type: str
    """One of: "invoice_received", "po_updated", "grn_created"."""
    payload: dict
    """Raw event data. Schema depends on event_type — see handler docstrings."""
    source_system: str
    """Originating system identifier, e.g. "SAP", "Oracle", "Coupa"."""
    timestamp: datetime


class WebhookResponse(BaseModel):
    """Standard response envelope for all webhook endpoints."""

    status: str
    message: str
    exception_id: str | None = None


@app.post(
    "/webhook/invoice",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_invoice_event(payload: WebhookPayload) -> WebhookResponse:
    """Handle an incoming invoice event.

    Expected payload keys:
        invoice_id, supplier_id, supplier_name, po_number, invoice_date,
        currency, line_items (list), tax_amount, freight_amount, total_amount.

    On receipt, the handler:
    1. Parses the payload into an Invoice model
    2. Looks up the corresponding PO from Redis (or returns 422 if missing)
    3. Enqueues a pipeline run (async)
    4. Returns 202 Accepted with the new exception_id

    Args:
        payload: The inbound webhook payload.

    Returns:
        WebhookResponse with status "accepted" and the new exception_id.
    """
    raise NotImplementedError


@app.post(
    "/webhook/po",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_po_event(payload: WebhookPayload) -> WebhookResponse:
    """Handle an incoming PO creation or update event.

    Expected payload keys:
        po_number, supplier_id, buyer_id, created_date, currency, line_items,
        tax_amount, freight_amount, total_amount.

    On receipt, the handler persists the PO to Redis under key "po:<po_number>"
    so it is available for invoice matching.

    Args:
        payload: The inbound webhook payload.

    Returns:
        WebhookResponse with status "stored".
    """
    raise NotImplementedError


@app.post(
    "/webhook/grn",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_grn_event(payload: WebhookPayload) -> WebhookResponse:
    """Handle an incoming Goods Receipt Note event.

    Expected payload keys:
        grn_id, po_number, supplier_id, receipt_date, line_items.

    Persists the GRN to Redis. If a MISSING_RECEIPT exception exists for the
    referenced PO, re-triggers the pipeline for that exception.

    Args:
        payload: The inbound webhook payload.

    Returns:
        WebhookResponse with status "stored" or "retriggered".
    """
    raise NotImplementedError


@app.get("/health")
async def health() -> dict:
    """Liveness probe endpoint.

    Returns:
        Dict with "status": "ok" and the current UTC timestamp.
    """
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
