from __future__ import annotations

import csv
import logging
from decimal import Decimal
from pathlib import Path

from models.grn import GoodsReceiptNote, GRNLineItem
from models.invoice import Invoice, LineItem
from models.purchase_order import POLineItem, PurchaseOrder

logger = logging.getLogger(__name__)

# Expected column sets for schema validation.
INVOICE_REQUIRED_COLUMNS = {
    "invoice_id", "supplier_id", "supplier_name", "po_number",
    "invoice_date", "currency", "sku", "description",
    "quantity", "unit_price", "line_total", "unit_of_measure",
    "tax_amount", "freight_amount", "total_amount",
}

PO_REQUIRED_COLUMNS = {
    "po_number", "supplier_id", "supplier_name", "buyer_id",
    "created_date", "currency", "sku", "description",
    "quantity", "unit_price", "line_total", "unit_of_measure",
    "tax_amount", "freight_amount", "total_amount",
}

GRN_REQUIRED_COLUMNS = {
    "grn_id", "po_number", "supplier_id", "receipt_date",
    "sku", "quantity_received", "condition",
}


class IngestValidationError(Exception):
    """Raised when a CSV file doesn't match the expected schema."""


def ingest_from_csv(
    invoice_path: str,
    po_path: str,
    grn_path: str | None = None,
) -> list[tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None]]:
    """Read CSV files and return matched (Invoice, PO, GRN) tuples.

    Each Invoice is matched to a PO via po_number. GRNs are matched to POs
    via po_number as well. Invoices with no matching PO raise IngestValidationError.
    Invoices with no matching GRN are returned with grn=None.

    See data/README.md for the expected CSV column schemas.

    Args:
        invoice_path: Path to invoices.csv.
        po_path: Path to purchase_orders.csv.
        grn_path: Optional path to grns.csv.

    Returns:
        List of (Invoice, PurchaseOrder, GoodsReceiptNote | None) tuples,
        one per unique invoice_id.

    Raises:
        IngestValidationError: If required columns are missing from any file.
        FileNotFoundError: If a specified file does not exist.
    """
    raise NotImplementedError


def _parse_invoice_rows(rows: list[dict]) -> dict[str, Invoice]:
    """Group CSV rows by invoice_id and construct Invoice objects.

    Args:
        rows: List of dicts from csv.DictReader for invoices.csv.

    Returns:
        Dict mapping invoice_id → Invoice.
    """
    raise NotImplementedError


def _parse_po_rows(rows: list[dict]) -> dict[str, PurchaseOrder]:
    """Group CSV rows by po_number and construct PurchaseOrder objects.

    Args:
        rows: List of dicts from csv.DictReader for purchase_orders.csv.

    Returns:
        Dict mapping po_number → PurchaseOrder.
    """
    raise NotImplementedError


def _parse_grn_rows(rows: list[dict]) -> dict[str, GoodsReceiptNote]:
    """Group CSV rows by grn_id and construct GoodsReceiptNote objects.

    Then re-indexes by po_number for matching (assumes one GRN per PO;
    if multiple exist, the most recent by receipt_date is used).

    Args:
        rows: List of dicts from csv.DictReader for grns.csv.

    Returns:
        Dict mapping po_number → GoodsReceiptNote.
    """
    raise NotImplementedError


def _validate_columns(rows: list[dict], required: set[str], source: str) -> None:
    """Raise IngestValidationError if any required columns are missing.

    Args:
        rows: Parsed CSV rows (must have at least one row to extract headers).
        required: Set of required column names.
        source: File description for the error message (e.g. "invoices.csv").
    """
    raise NotImplementedError


def _read_csv(path: str) -> list[dict]:
    """Read a CSV file and return all rows as a list of dicts.

    Args:
        path: Absolute or relative path to the CSV file.

    Returns:
        List of row dicts (keys are column names).
    """
    raise NotImplementedError
