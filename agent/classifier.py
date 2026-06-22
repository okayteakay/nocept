from __future__ import annotations

import logging

from pydantic import BaseModel

from config.settings import AppConfig
from models.exception import InvoiceException, LineItemVariance
from models.exception_record import ExceptionType
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class ClassificationResult(BaseModel):
    """Output of the classification step."""

    exception_types: list[ExceptionType]
    line_variances: list[LineItemVariance]
    total_variance_usd: float
    informal_modification_signals: list[str]
    """Human-readable strings explaining why informal modification is suspected."""


def classify_exception(
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
    config: AppConfig,
    store: RedisStateStore | None = None,
    exception_id: str | None = None,
) -> ClassificationResult:
    """Perform three-way matching and classify all detected mismatch types.

    Steps:
    1. Check for missing GRN → MISSING_GOODS_RECEIPT
    2. Check for duplicate submission against existing Redis records (if store provided)
    3. Compute per-line variances between invoice and PO
    4. Classify price variances outside tolerance → PRICE_VARIANCE
    5. Classify quantity variances outside tolerance → QUANTITY_VARIANCE
    6. Apply informal-modification heuristics → INFORMAL_MODIFICATION

    Heuristics for INFORMAL_MODIFICATION:
    - SKU on invoice not present on PO (new SKU substitution)
    - Partial quantity shortfall on PO SKU paired with a new invoice SKU
    - Product grade/tier change on a shared SKU
    - Expedited shipping surcharge (SHIP-EXP) added without PO authorization

    Args:
        invoice: The supplier invoice to validate.
        po: The Purchase Order it references.
        grn: The Goods Receipt Note, or None if not yet received.
        config: AppConfig for tolerance thresholds.
        store: Optional RedisStateStore for duplicate detection.
        exception_id: Optional ID of current exception to exclude from duplicate check.

    Returns:
        ClassificationResult with all detected exception types and variances.
    """
    exception_types: list[ExceptionType] = []

    # 1. Missing GRN
    if grn is None:
        exception_types.append(ExceptionType.MISSING_GOODS_RECEIPT)
        logger.debug("Invoice %s: missing GRN for PO %s", invoice.invoice_number, po.po_number)

    # 2. Duplicate detection (requires store). Boundary: Redis. If Redis is
    #    down, fail open (continue classification without duplicate flag).
    if store is not None:
        try:
            if check_duplicate(invoice, store, exclude_exception_id=exception_id):
                exception_types.append(ExceptionType.DUPLICATE_INVOICE)
                logger.info(
                    "Invoice %s flagged as duplicate for supplier %s",
                    invoice.invoice_number,
                    invoice.supplier_id,
                )
                # Return early — no line-level analysis needed for duplicates
                return ClassificationResult(
                    exception_types=exception_types,
                    line_variances=[],
                    total_variance_usd=0.0,
                    informal_modification_signals=[],
                )
        except Exception as e:
            logger.warning(f"Duplicate check failed for invoice {invoice.invoice_number}: {e}")
            # Continue with classification anyway

    # 3. Compute per-line variances (pure function; let it raise on bugs)
    variances = _compute_line_variances(invoice, po)

    # 4. Price variance — any non-new SKU with |price delta| > tolerance
    price_variances = [
        v
        for v in variances
        if (
            not v.is_new_sku
            and v.price_delta_pct is not None
            and abs(v.price_delta_pct) > config.price_tolerance_pct
        )
    ]
    if price_variances:
        exception_types.append(ExceptionType.PRICE_VARIANCE)
        logger.debug(
            "Invoice %s: %d price variance line(s)", invoice.invoice_number, len(price_variances)
        )

    # 5. Quantity variance — any non-new SKU with quantity delta > tolerance (as % of PO qty)
    qty_variances = [
        v
        for v in variances
        if (
            not v.is_new_sku
            and v.quantity_delta is not None
            and v.po_quantity is not None
            and v.po_quantity > 0
            and abs(v.quantity_delta) / v.po_quantity > config.qty_tolerance_pct
        )
    ]
    if qty_variances:
        exception_types.append(ExceptionType.QUANTITY_VARIANCE)
        logger.debug(
            "Invoice %s: %d quantity variance line(s)", invoice.invoice_number, len(qty_variances)
        )

    # 6. Informal modification heuristics (pure function; let it raise on bugs)
    signals = _detect_informal_modification_signals(variances, po, invoice)
    if signals:
        exception_types.append(ExceptionType.INFORMAL_MODIFICATION)
        logger.debug(
            "Invoice %s: informal modification signals: %s",
            invoice.invoice_number,
            signals,
        )

    # Dollar variance: absolute difference between invoice and PO totals
    total_variance_usd = round(abs(invoice.total_amount - po.total_amount), 2)

    return ClassificationResult(
        exception_types=exception_types,
        line_variances=variances,
        total_variance_usd=total_variance_usd,
        informal_modification_signals=signals,
    )


def _compute_line_variances(
    invoice: Invoice,
    po: PurchaseOrder,
) -> list[LineItemVariance]:
    """Build a LineItemVariance for every SKU present on the invoice or PO.

    For SKUs on the invoice but not the PO, ``is_new_sku=True``.
    For SKUs on the PO but missing from the invoice, ``invoice_quantity=None``.
    Expedited shipping lines (SKU "SHIP-EXP" or description containing
    "expedited") are tagged ``is_expedited_shipping=True``.

    Args:
        invoice: The supplier invoice.
        po: The Purchase Order.

    Returns:
        List of LineItemVariance, one per unique SKU across both documents.
    """
    all_skus = {item.sku for item in invoice.line_items} | {item.sku for item in po.line_items}
    variances: list[LineItemVariance] = []

    for sku in sorted(all_skus):  # stable ordering
        inv_item = invoice.line_item_by_sku(sku)
        po_item = po.line_item_by_sku(sku)

        quantity_delta: int | None = None
        price_delta_pct: float | None = None

        if inv_item is not None and po_item is not None:
            quantity_delta = inv_item.quantity - po_item.quantity
            if po_item.unit_price != 0:
                price_delta_pct = (
                    inv_item.unit_price - po_item.unit_price
                ) / po_item.unit_price

        is_new_sku = inv_item is not None and po_item is None

        # Expedited shipping: exact SKU match or description keyword
        ref_item = inv_item or po_item
        is_expedited = (
            sku == "SHIP-EXP"
            or (ref_item is not None and "expedited" in (ref_item.description or "").lower())
        )

        variances.append(
            LineItemVariance(
                sku=sku,
                description=(inv_item or po_item).description if (inv_item or po_item) else "Unknown",
                po_quantity=po_item.quantity if po_item else None,
                invoice_quantity=inv_item.quantity if inv_item else None,
                po_unit_price=po_item.unit_price if po_item else None,
                invoice_unit_price=inv_item.unit_price if inv_item else None,
                quantity_delta=quantity_delta,
                price_delta_pct=price_delta_pct,
                is_new_sku=is_new_sku,
                is_expedited_shipping=is_expedited,
            )
        )

    return variances


def _detect_informal_modification_signals(
    variances: list[LineItemVariance],
    po: PurchaseOrder,
    invoice: Invoice,
) -> list[str]:
    """Scan line variances for patterns suggesting an undocumented modification.

    Returns human-readable signal descriptions. An empty list means no signals.

    Signals checked:
    1. New non-expedited SKU on invoice not on PO (substitution)
    2. Expedited shipping surcharge added without PO authorization
    3. Quantity shortfall on a PO SKU paired with a new invoice SKU (swap pattern)
    4. Product grade change on a shared SKU (``product_grade`` field differs)
    5. Invoice total exceeds PO total with no new SKUs (pure price uplift ≥ 1%)

    Args:
        variances: Computed line variances from ``_compute_line_variances``.
        po: The Purchase Order.
        invoice: The supplier invoice.

    Returns:
        List of signal description strings.
    """
    signals: list[str] = []

    new_non_shipping_skus = [
        v for v in variances if v.is_new_sku and not v.is_expedited_shipping
    ]
    expedited_skus = [v for v in variances if v.is_expedited_shipping and v.is_new_sku]

    # Signal 1: New substitute SKU
    for v in new_non_shipping_skus:
        signals.append(
            f"Invoice contains SKU {v.sku!r} ({v.description!r}) not present on PO"
        )

    # Signal 2: Expedited shipping surcharge added
    for v in expedited_skus:
        signals.append(
            f"Expedited shipping surcharge SKU {v.sku!r} added to invoice — not on PO"
        )

    # Signal 3: Substitution swap pattern — PO SKU short-shipped + new SKU appeared
    shortfall_skus = [
        v
        for v in variances
        if (
            not v.is_new_sku
            and v.quantity_delta is not None
            and v.quantity_delta < 0  # fewer than ordered
        )
    ]
    if shortfall_skus and new_non_shipping_skus:
        for short_v in shortfall_skus:
            signals.append(
                f"Substitution pattern: PO SKU {short_v.sku!r} invoiced at "
                f"{short_v.invoice_quantity} vs ordered {short_v.po_quantity} "
                f"({-short_v.quantity_delta} unit(s) substituted with new SKU)"
            )

    # Signal 4: Product grade/tier change on shared SKUs
    for v in variances:
        if v.is_new_sku:
            continue
        inv_item = invoice.line_item_by_sku(v.sku)
        po_item = po.line_item_by_sku(v.sku)
        if (
            inv_item is not None
            and po_item is not None
            and inv_item.product_grade != po_item.product_grade
        ):
            signals.append(
                f"Product grade changed for SKU {v.sku!r}: "
                f"PO grade {po_item.product_grade!r} → invoice grade {inv_item.product_grade!r}"
            )

    # Signal 5: Pure price uplift (no new SKUs, invoice > PO by ≥ 1%)
    if not new_non_shipping_skus and not expedited_skus and invoice.total_amount > po.total_amount:
        uplift_pct = (invoice.total_amount - po.total_amount) / po.total_amount * 100
        if uplift_pct >= 1.0:
            signals.append(
                f"Invoice total ${invoice.total_amount:,.2f} exceeds PO total "
                f"${po.total_amount:,.2f} ({uplift_pct:.1f}% uplift) with no new SKUs"
            )

    return signals


def check_duplicate(invoice: Invoice, store: RedisStateStore, exclude_exception_id: str | None = None) -> bool:
    """Return True if this invoice is a duplicate of a prior exception for the supplier.

    Duplicate detection fingerprint: ``(supplier_id, invoice_number, total_amount)``.
    Two different suppliers can issue the same invoice number legitimately, and
    a re-issued invoice with a corrected amount is *not* a duplicate. A duplicate
    is the same supplier, same invoice number, and same total amount.

    This is a linear scan over the supplier's exception history — fine for
    moderate volumes. For high-volume production, replace with a Redis SET
    keyed by the fingerprint:

        key = f"dup:{invoice.supplier_id}:{invoice.invoice_number}:{round(invoice.total_amount, 2)}"
        return bool(r.exists(key))

    Args:
        invoice: The invoice to check.
        store: The Redis state store to query.
        exclude_exception_id: Optional exception ID to skip (for checking without finding self).

    Returns:
        True if a prior exception with the same (supplier_id, invoice_number,
        total_amount) triple is found. Returns False if Redis is unavailable
        (fail open — see note below).

    Note:
        Fails open on Redis errors. For a real AP system, you may want to
        fail closed (assume duplicate on error) and have a human review the
        borderline cases. Choice depends on the cost of false positives vs.
        false negatives for your business.
    """
    try:
        existing = store.list_by_supplier(invoice.supplier_id)
        for e in existing:
            # Skip the current exception if provided (avoid self-duplicate)
            if exclude_exception_id and e.exception_id == exclude_exception_id:
                continue
            if (
                e.invoice.invoice_number == invoice.invoice_number
                and abs(float(e.invoice.total_amount) - float(invoice.total_amount)) < 0.01
            ):
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking for duplicates in Redis for supplier {invoice.supplier_id}: {e}")
        return False
