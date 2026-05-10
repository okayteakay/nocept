"""Webhook receiver for invoice, PO, and GRN events from ERP systems (SAP S/4HANA).

Validates incoming webhooks via HMAC-SHA256 signature, parses SAP payloads,
enqueues async pipeline tasks in Celery.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis as redis_lib
from fastapi import Depends, FastAPI, HTTPException, Header, status
from pydantic import BaseModel

from audit.audit_logger import AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from config.settings import AppConfig, get_settings
from ingestion.sap_mapper import map_sap_invoice, map_sap_po, map_sap_grn
from models.exception import ExceptionState, InvoiceException
from state.redis_backend import RedisStateStore
from worker.tasks import process_exception

logger = logging.getLogger(__name__)

_res: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifespan: initialise Redis connection and state store."""
    cfg = get_settings()
    cfg.configure_logging()

    r = get_redis_connection(cfg.redis_url)
    streams = RedisStreamsClient(r, "ap:audit:events")

    _res.update(
        {
            "cfg": cfg,
            "r": r,
            "store": RedisStateStore(r),
            "audit": AuditLogger(streams),
        }
    )

    logger.info("Webhook receiver ready — Redis connected, state store initialised.")
    yield

    _res.clear()


app = FastAPI(
    title="Invoice Exception Webhook Receiver",
    description="Receives invoice, PO, and GRN events from SAP S/4HANA and routes them to the async resolution pipeline.",
    version="0.2.0",
    lifespan=lifespan,
)


def _get_cfg() -> AppConfig:
    return _res["cfg"]


def _get_r() -> redis_lib.Redis:
    return _res["r"]


def _get_store() -> RedisStateStore:
    return _res["store"]


def _get_audit() -> AuditLogger:
    return _res["audit"]


class WebhookPayload(BaseModel):
    """Inbound event payload from an ERP system."""

    event_type: str
    """One of: "invoice_received", "po_updated", "grn_created"."""
    payload: dict
    """Raw event data. Schema depends on event_type — see handler docstrings."""
    source_system: str
    """Originating system identifier, e.g. "SAP"."""
    timestamp: datetime


class WebhookResponse(BaseModel):
    """Standard response envelope for all webhook endpoints."""

    status: str
    message: str
    exception_id: str | None = None
    task_id: str | None = None


def _verify_signature(
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature of webhook payload.

    Args:
        body: Raw request body
        signature: X-SAP-Signature header value (hex-encoded)
        secret: Shared secret from config

    Returns:
        True if signature matches, False otherwise
    """
    if not secret:
        logger.warning("No SAP_WEBHOOK_SECRET configured; signature verification disabled")
        return True

    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@app.post(
    "/webhook/po",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_po_event(
    payload: WebhookPayload,
    x_sap_signature: str | None = Header(None),
    cfg: AppConfig = Depends(_get_cfg),
    r: redis_lib.Redis = Depends(_get_r),
    audit: AuditLogger = Depends(_get_audit),
) -> WebhookResponse:
    """Handle an incoming PO creation or update event from SAP S/4HANA.

    Parses SAP EKKO/EKPO (PO header/line) payload, persists to Redis under
    key `po:<po_number>` so it is available for invoice matching.

    Args:
        payload: The inbound webhook payload
        x_sap_signature: HMAC-SHA256 signature (hex) for verification
        cfg: Application config
        r: Redis connection
        audit: Audit logger

    Returns:
        WebhookResponse with status "stored"
    """
    # Verify signature if configured
    if x_sap_signature:
        body = json.dumps(payload.model_dump(), default=str).encode()
        if not _verify_signature(body, x_sap_signature, cfg.sap_webhook_secret):
            logger.warning("PO webhook: signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

    logger.info(f"PO webhook received from {payload.source_system}")

    try:
        # Map SAP payload to PO model
        po = map_sap_po(payload.payload)
        logger.info(f"Mapped PO: {po.po_number} from supplier {po.supplier_id}")

        # Persist to Redis with 30-day TTL (covers typical invoice lead time + processing)
        r.set(
            f"po:{po.po_number}",
            po.model_dump_json(),
            ex=86400 * 30,
        )

        audit.log(
            audit.AuditEvent(
                event_type="po_received",
                details={
                    "po_number": po.po_number,
                    "supplier_id": po.supplier_id,
                    "total_amount": float(po.total_amount),
                },
            )
        )

        logger.info(f"PO {po.po_number} stored in Redis")

        return WebhookResponse(
            status="stored",
            message=f"PO {po.po_number} received and cached",
        )

    except Exception as e:
        logger.error(f"Error processing PO webhook: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse PO: {str(e)}",
        )


@app.post(
    "/webhook/invoice",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_invoice_event(
    payload: WebhookPayload,
    x_sap_signature: str | None = Header(None),
    cfg: AppConfig = Depends(_get_cfg),
    r: redis_lib.Redis = Depends(_get_r),
    store: RedisStateStore = Depends(_get_store),
    audit: AuditLogger = Depends(_get_audit),
) -> WebhookResponse:
    """Handle an incoming invoice event from SAP S/4HANA.

    Expected payload keys (SAP INVOIC IDoc format):
        invoice_id, supplier_id, supplier_name, po_number, invoice_date,
        currency, line_items (list), total_amount.

    On receipt:
    1. Parses the payload into an Invoice model
    2. Looks up the corresponding PO from Redis (returns 422 if missing)
    3. Looks up the corresponding GRN if present
    4. Creates and saves an InvoiceException with state RECEIVED
    5. Enqueues a Celery task for async pipeline processing
    6. Returns 202 Accepted with the new exception_id

    Args:
        payload: The inbound webhook payload
        x_sap_signature: HMAC-SHA256 signature for verification
        cfg: Application config
        r: Redis connection
        store: State store
        audit: Audit logger

    Returns:
        WebhookResponse with status "accepted" and exception_id
    """
    # Verify signature
    if x_sap_signature:
        body = json.dumps(payload.model_dump(), default=str).encode()
        if not _verify_signature(body, x_sap_signature, cfg.sap_webhook_secret):
            logger.warning("Invoice webhook: signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

    logger.info(f"Invoice webhook received from {payload.source_system}")

    try:
        # Map SAP payload to Invoice model
        invoice = map_sap_invoice(payload.payload)
        logger.info(f"Mapped invoice: {invoice.invoice_number} for PO {invoice.po_number}")

        # Look up PO from Redis
        po_key = f"po:{invoice.po_number}"
        po_json = r.get(po_key)
        if po_json is None:
            logger.warning(f"PO {invoice.po_number} not found in Redis")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PO {invoice.po_number} not found. Call /webhook/po first or wait for PO sync.",
            )

        from models.purchase_order import PurchaseOrder

        po_str = po_json if isinstance(po_json, str) else po_json.decode()
        po = PurchaseOrder.model_validate_json(po_str)

        # Look up GRN if present
        grn = None
        grn_key = f"grn:{invoice.po_number}"
        grn_json = r.get(grn_key)
        if grn_json:
            from models.grn import GoodsReceiptNote

            grn_str = grn_json if isinstance(grn_json, str) else grn_json.decode()
            grn = GoodsReceiptNote.model_validate_json(grn_str)

        # Create exception
        exc = InvoiceException(
            invoice=invoice,
            purchase_order=po,
            grn=grn,
            state=ExceptionState.RECEIVED,
        )

        # Save exception to state store
        store.save(exc)
        logger.info(f"Exception {exc.exception_id} created and saved")

        # Audit event
        audit.log(
            audit.AuditEvent(
                exception_id=exc.exception_id,
                event_type="webhook_received",
                details={
                    "invoice_number": invoice.invoice_number,
                    "po_number": invoice.po_number,
                    "supplier_id": invoice.supplier_id,
                    "total_amount": float(invoice.total_amount),
                },
            )
        )

        # Enqueue async pipeline task
        task = process_exception.delay(exc.exception_id)
        logger.info(f"Enqueued Celery task {task.id} for exception {exc.exception_id}")

        return WebhookResponse(
            status="accepted",
            message=f"Invoice {invoice.invoice_number} accepted for processing",
            exception_id=exc.exception_id,
            task_id=str(task.id),
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            f"Error processing invoice webhook: {type(e).__name__}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse invoice: {str(e)}",
        )


@app.post(
    "/webhook/grn",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def receive_grn_event(
    payload: WebhookPayload,
    x_sap_signature: str | None = Header(None),
    cfg: AppConfig = Depends(_get_cfg),
    r: redis_lib.Redis = Depends(_get_r),
    store: RedisStateStore = Depends(_get_store),
    audit: AuditLogger = Depends(_get_audit),
) -> WebhookResponse:
    """Handle an incoming Goods Receipt Note event from SAP S/4HANA.

    Expected payload keys (SAP MBLNR/MSEG format):
        grn_id, po_number, supplier_id, receipt_date, line_items.

    On receipt:
    1. Parses the payload into a GoodsReceiptNote model
    2. Persists to Redis under key `grn:<po_number>`
    3. Checks for any exception referencing this PO with MISSING_GOODS_RECEIPT type
    4. If found, updates the exception's GRN field and re-triggers the pipeline
    5. Returns 200 with status "stored" or "retriggered"

    Args:
        payload: The inbound webhook payload
        x_sap_signature: HMAC-SHA256 signature
        cfg: Application config
        r: Redis connection
        store: State store
        audit: Audit logger

    Returns:
        WebhookResponse with status "stored" or "retriggered"
    """
    # Verify signature
    if x_sap_signature:
        body = json.dumps(payload.model_dump(), default=str).encode()
        if not _verify_signature(body, x_sap_signature, cfg.sap_webhook_secret):
            logger.warning("GRN webhook: signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

    logger.info(f"GRN webhook received from {payload.source_system}")

    try:
        # Map SAP payload to GRN model
        grn = map_sap_grn(payload.payload)
        logger.info(f"Mapped GRN: {grn.gr_number} for PO {grn.po_number}")

        # Persist to Redis with 30-day TTL
        r.set(
            f"grn:{grn.po_number}",
            grn.model_dump_json(),
            ex=86400 * 30,
        )

        audit.log(
            audit.AuditEvent(
                event_type="grn_received",
                details={
                    "gr_number": grn.gr_number,
                    "po_number": grn.po_number,
                    "supplier_id": grn.supplier_id,
                },
            )
        )

        logger.info(f"GRN {grn.gr_number} stored in Redis")

        # Check for MISSING_GOODS_RECEIPT exceptions for this PO
        exceptions_received = store.list_by_state(ExceptionState.RECEIVED)
        exceptions_triaged = store.list_by_state(ExceptionState.TRIAGED)
        all_exc_ids = set(exceptions_received + exceptions_triaged)

        retriggered_count = 0
        for exc_id in all_exc_ids:
            try:
                exc = store.load(exc_id)
                from models.exception import ExceptionType

                # Check if this exception is for the same PO and has MISSING_GOODS_RECEIPT
                if (
                    exc.purchase_order.po_number == grn.po_number
                    and ExceptionType.MISSING_GOODS_RECEIPT in exc.exception_types
                ):
                    # Update GRN and re-trigger
                    exc.grn = grn
                    store.save(exc)

                    task = process_exception.delay(exc_id)
                    logger.info(
                        f"Re-triggered exception {exc_id} with GRN {grn.gr_number} "
                        f"(task {task.id})"
                    )
                    retriggered_count += 1

            except KeyError:
                # Exception was deleted or doesn't exist
                pass
            except Exception as e:
                logger.warning(f"Error re-triggering exception {exc_id}: {e}")

        status_msg = "stored"
        msg = f"GRN {grn.gr_number} received and cached"
        if retriggered_count > 0:
            status_msg = "retriggered"
            msg += f"; re-triggered {retriggered_count} MISSING_GOODS_RECEIPT exception(s)"

        return WebhookResponse(
            status=status_msg,
            message=msg,
        )

    except Exception as e:
        logger.error(f"Error processing GRN webhook: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse GRN: {str(e)}",
        )


@app.get("/health", tags=["Health"])
async def health() -> dict:
    """Liveness probe endpoint.

    Returns:
        Dict with "status": "ok" and the current UTC timestamp.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "webhook-receiver",
    }
