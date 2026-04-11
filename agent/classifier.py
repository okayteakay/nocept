from __future__ import annotations

import logging
from decimal import Decimal

from pydantic import BaseModel

from config.settings import AppConfig
from models.exception import ExceptionType, InvoiceException, LineItemVariance
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from state.redis_backend import RedisStateStore

logger = logging.getLogger(__name__)


class ClassificationResult(BaseModel):
    """Output of the classification step."""

    exception_types: list[ExceptionType]
    line_variances: list[LineItemVariance]
    total_variance_usd: Decimal
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
    1. Check for missing GRN → MISSING_RECEIPT
    2. Check for duplicate submission against existing Redis records (if store provided)
    3. Compute per-line variances between invoice and PO
    4. Classify price, quantity, tax, freight, and currency variances
    5. Apply informal-modification heuristics to line variances
    6. Return aggregated ClassificationResult

    Heuristics for INFORMAL_MODIFICATION:
    - SKU on invoice not present on PO (new SKU substitution)
    - Partial quantity on one PO SKU with a compensating new SKU at a higher price
    - Grade/tier shift: description contains "Grade A" on PO but "Grade B" on invoice

    Args:
        invoice: The supplier invoice to validate.
        po: The internal Purchase Order it references.
        grn: The Goods Receipt Note, or None if not yet received.
        config: AppConfig for tolerance thresholds.
        store: Optional RedisStateStore for duplicate detection.

    Returns:
        ClassificationResult with all detected exception types and variances.
    """
    raise NotImplementedError


def _compute_line_variances(
    invoice: Invoice,
    po: PurchaseOrder,
) -> list[LineItemVariance]:
    """Build a LineItemVariance for every SKU present on the invoice or PO.

    For SKUs on the invoice but not the PO, is_new_sku=True.
    For SKUs on the PO but not the invoice, invoice_quantity=None.

    Args:
        invoice: The supplier invoice.
        po: The Purchase Order.

    Returns:
        List of LineItemVariance, one per unique SKU across both documents.
    """
    raise NotImplementedError


def _detect_informal_modification_signals(
    variances: list[LineItemVariance],
    po: PurchaseOrder,
    invoice: Invoice,
) -> list[str]:
    """Scan line variances for patterns suggesting an undocumented modification.

    Returns a list of human-readable signal descriptions. An empty list means
    no informal modification signals were detected.

    Signals checked:
    - New SKU on invoice not on PO
    - Partial quantity shortfall on PO SKU paired with a new SKU (substitution pattern)
    - Description grade/tier change (e.g., "Grade A" → "Grade B")
    - Total invoice > total PO despite same or fewer items (price uplift)

    Args:
        variances: Computed line variances.
        po: The Purchase Order.
        invoice: The supplier invoice.

    Returns:
        List of signal description strings.
    """
    raise NotImplementedError


def _check_duplicate(invoice: Invoice, store: RedisStateStore) -> bool:
    """Return True if an exception for this invoice ID already exists in Redis.

    Args:
        invoice: The invoice to check.
        store: The Redis state store to query.

    Returns:
        True if a duplicate is detected.
    """
    raise NotImplementedError
