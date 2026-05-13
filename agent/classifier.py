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

    Returns:
        ClassificationResult with all detected exception types and variances.
    """
    try:
        exception_types: list[ExceptionType] = []

        # 1. Missing GRN
        if grn is None:
            exception_types.append(ExceptionType.MISSING_GOODS_RECEIPT)
            logger.debug("Invoice %s: missing GRN for PO %s", invoice.invoice_number, po.po_number)

        # 2. Duplicate detection (requires store)
        if store is not None:
            try:
                if _check_duplicate(invoice, store):
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

        # 3. Compute per-line variances
        try:
            variances = _compute_line_variances(invoice, po)
        except Exception as e:
            logger.error(f"Error computing line variances for invoice {invoice.invoice_number}: {e}", exc_info=True)
            variances = []

        # 4. Price variance — any non-new SKU with |price delta| > tolerance
        try:
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
        except Exception as e:
            logger.error(f"Error detecting price variances for invoice {invoice.invoice_number}: {e}")

        # 5. Quantity variance — any non-new SKU with quantity delta > tolerance (as % of PO qty)
        try:
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
        except Exception as e:
            logger.error(f"Error detecting quantity variances for invoice {invoice.invoice_number}: {e}")

        # 6. Informal modification heuristics
        try:
            signals = _detect_informal_modification_signals(variances, po, invoice)
            if signals:
                exception_types.append(ExceptionType.INFORMAL_MODIFICATION)
                logger.debug(
                    "Invoice %s: informal modification signals: %s",
                    invoice.invoice_number,
                    signals,
                )
        except Exception as e:
            logger.error(f"Error detecting informal modifications for invoice {invoice.invoice_number}: {e}", exc_info=True)
            signals = []

        # Dollar variance: absolute difference between invoice and PO totals
        try:
            total_variance_usd = round(abs(invoice.total_amount - po.total_amount), 2)
        except Exception as e:
            logger.error(f"Error computing total variance for invoice {invoice.invoice_number}: {e}")
            total_variance_usd = 0.0

        return ClassificationResult(
            exception_types=exception_types,
            line_variances=variances,
            total_variance_usd=total_variance_usd,
            informal_modification_signals=signals,
        )
    except Exception as e:
        logger.error(f"Unexpected error classifying invoice {invoice.invoice_number}: {e}", exc_info=True)
        # Return a minimal result to prevent pipeline crash
        return ClassificationResult(
            exception_types=[],
            line_variances=[],
            total_variance_usd=0.0,
            informal_modification_signals=["Classification error: see logs"],
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
    try:
        all_skus = {item.sku for item in invoice.line_items} | {item.sku for item in po.line_items}
        variances: list[LineItemVariance] = []

        for sku in sorted(all_skus):  # stable ordering
            try:
                inv_item = invoice.line_item_by_sku(sku)
                po_item = po.line_item_by_sku(sku)

                quantity_delta: int | None = None
                price_delta_pct: float | None = None

                if inv_item is not None and po_item is not None:
                    try:
                        quantity_delta = inv_item.quantity - po_item.quantity
                        if po_item.unit_price != 0:
                            price_delta_pct = (
                                inv_item.unit_price - po_item.unit_price
                            ) / po_item.unit_price
                    except (ValueError, ZeroDivisionError, AttributeError) as e:
                        logger.warning(f"Error computing variance for SKU {sku}: {e}")

                is_new_sku = inv_item is not None and po_item is None

                # Expedited shipping: exact SKU match or description keyword
                ref_item = inv_item or po_item
                is_expedited = False
                try:
                    is_expedited = sku == "SHIP-EXP" or (
                        ref_item is not None
                        and "expedited" in (ref_item.description or "").lower()
                    )
                except AttributeError:
                    pass

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
            except Exception as e:
                logger.warning(f"Error processing SKU {sku}: {e}")
                continue

        return variances
    except Exception as e:
        logger.error(f"Error computing line variances: {e}", exc_info=True)
        return []


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


def _check_duplicate(invoice: Invoice, store: RedisStateStore) -> bool:
    """Return True if an exception for this invoice number already exists in Redis.

    Loads all exceptions for the same supplier and compares invoice numbers.
    Suitable for datasets of moderate size; for high-volume production use a
    dedicated ``invoice_index:<invoice_number>`` key instead.

    Args:
        invoice: The invoice to check.
        store: The Redis state store to query.

    Returns:
        True if a prior exception with the same invoice number is found.
        Returns False if Redis is unavailable (fails open).
    """
    try:
        existing = store.list_by_supplier(invoice.supplier_id)
        return any(
            e.invoice.invoice_number == invoice.invoice_number for e in existing
        )
    except Exception as e:
        logger.error(f"Error checking for duplicates in Redis for supplier {invoice.supplier_id}: {e}")
        # Fail open: assume not a duplicate if Redis is down
        return False
