"""JSON Dataset Ingestor.

Loads the Meridian Corp synthetic AP dataset from the dataset/data/ directory
and returns strongly-typed model objects. All seven data files are supported.

The primary entry point is load_dataset(), which returns a DatasetBundle
with all documents pre-cross-linked for pipeline consumption.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.communication import Email, PhoneTranscript
from models.exception_record import ExceptionRecord, ExceptionType
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from models.supplier import Catalog, Supplier, load_catalog

logger = logging.getLogger(__name__)

# Default path to the dataset data directory (relative to project root)
DEFAULT_DATA_DIR = Path(__file__).parent.parent / "dataset" / "data"
DEFAULT_CATALOG_PATH = Path(__file__).parent.parent / "dataset" / "catalog.json"


@dataclass
class DatasetBundle:
    """All loaded dataset objects, cross-linked and ready for pipeline consumption.

    The primary join key across all collections is po_number. Invoices and GRs
    additionally link to their ExceptionRecord via invoice_number.
    """

    invoices: dict[str, Invoice]               # keyed by invoice_number
    purchase_orders: dict[str, PurchaseOrder]  # keyed by po_number
    goods_receipts: dict[str, GoodsReceiptNote]  # keyed by po_number (one GR per PO)
    exception_records: dict[str, ExceptionRecord]  # keyed by exception_id
    suppliers: dict[str, Supplier]             # keyed by supplier_id
    emails: dict[str, Email]                   # keyed by email_id
    phone_transcripts: dict[str, PhoneTranscript]  # keyed by transcript_id
    catalog: Catalog | None = None

    # Derived lookup: invoice_number → ExceptionRecord (only for actual exceptions)
    _exc_by_invoice: dict[str, ExceptionRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._exc_by_invoice = {
            rec.invoice_number: rec
            for rec in self.exception_records.values()
            if rec.is_exception
        }

    def exception_for_invoice(self, invoice_number: str) -> ExceptionRecord | None:
        """Return the ExceptionRecord for the given invoice, or None for clean invoices."""
        return self._exc_by_invoice.get(invoice_number)

    def emails_for_exception(self, record: ExceptionRecord) -> list[Email]:
        """Return all Email objects linked to the given ExceptionRecord."""
        return [self.emails[eid] for eid in record.related_email_ids if eid in self.emails]

    def transcripts_for_exception(self, record: ExceptionRecord) -> list[PhoneTranscript]:
        """Return all PhoneTranscript objects linked to the given ExceptionRecord."""
        return [
            self.phone_transcripts[tid]
            for tid in record.related_transcript_ids
            if tid in self.phone_transcripts
        ]

    def exception_triples(
        self,
    ) -> list[tuple[Invoice, PurchaseOrder, GoodsReceiptNote | None, ExceptionRecord]]:
        """Return all (Invoice, PO, GR, ExceptionRecord) tuples for actual exceptions.

        Used to feed the agent pipeline with pre-labeled exception data.
        GR is None for MISSING_GOODS_RECEIPT exceptions.
        """
        result = []
        for rec in self.exception_records.values():
            if not rec.is_exception:
                continue
            invoice = self.invoices.get(rec.invoice_number)
            po = self.purchase_orders.get(rec.po_number)
            if invoice is None or po is None:
                logger.warning(
                    "Exception %s references missing invoice %s or PO %s — skipping",
                    rec.exception_id,
                    rec.invoice_number,
                    rec.po_number,
                )
                continue
            gr = self.goods_receipts.get(rec.po_number)
            result.append((invoice, po, gr, rec))
        return result

    @property
    def exception_count(self) -> int:
        """Total number of actual exceptions (excludes NONE records)."""
        return len(self._exc_by_invoice)

    @property
    def exception_type_counts(self) -> dict[str, int]:
        """Count of exceptions by type."""
        counts: dict[str, int] = {}
        for rec in self._exc_by_invoice.values():
            key = rec.exception_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts


def load_dataset(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> DatasetBundle:
    """Load the complete Meridian Corp dataset from JSON files.

    Args:
        data_dir: Directory containing the seven dataset JSON files.
        catalog_path: Path to catalog.json with product hierarchy.

    Returns:
        A DatasetBundle with all documents loaded and cross-linked.

    Raises:
        FileNotFoundError: If any required data file is missing.
        ValidationError: If any record fails Pydantic validation.
    """
    data_dir = Path(data_dir)
    catalog_path = Path(catalog_path)

    invoices = _load_invoices(data_dir / "invoices.json")
    purchase_orders = _load_purchase_orders(data_dir / "purchase_orders.json")
    goods_receipts = _load_goods_receipts(data_dir / "goods_receipts.json")
    exception_records = _load_exception_records(data_dir / "exception_records.json")
    suppliers = _load_suppliers(data_dir / "suppliers.json")
    emails = _load_emails(data_dir / "emails.json")
    phone_transcripts = _load_phone_transcripts(data_dir / "phone_transcripts.json")

    catalog: Catalog | None = None
    if catalog_path.exists():
        try:
            catalog = load_catalog(catalog_path)
            logger.info("Catalog loaded: %d suppliers", len(catalog.suppliers))
        except Exception as exc:
            logger.warning("Could not load catalog from %s: %s", catalog_path, exc)

    bundle = DatasetBundle(
        invoices=invoices,
        purchase_orders=purchase_orders,
        goods_receipts=goods_receipts,
        exception_records=exception_records,
        suppliers=suppliers,
        emails=emails,
        phone_transcripts=phone_transcripts,
        catalog=catalog,
    )

    logger.info(
        "Dataset loaded: %d invoices, %d POs, %d GRs, %d exceptions (%s), "
        "%d emails, %d transcripts",
        len(invoices),
        len(purchase_orders),
        len(goods_receipts),
        bundle.exception_count,
        bundle.exception_type_counts,
        len(emails),
        len(phone_transcripts),
    )
    return bundle


# ---------------------------------------------------------------------------
# Private per-file loaders
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _load_invoices(path: Path) -> dict[str, Invoice]:
    records = _read_json(path)
    result: dict[str, Invoice] = {}
    for raw in records:
        try:
            inv = Invoice.model_validate(raw)
            result[inv.invoice_number] = inv
        except Exception as exc:
            logger.warning("Skipping invalid invoice %s: %s", raw.get("invoice_number"), exc)
    return result


def _load_purchase_orders(path: Path) -> dict[str, PurchaseOrder]:
    records = _read_json(path)
    result: dict[str, PurchaseOrder] = {}
    for raw in records:
        try:
            po = PurchaseOrder.model_validate(raw)
            result[po.po_number] = po
        except Exception as exc:
            logger.warning("Skipping invalid PO %s: %s", raw.get("po_number"), exc)
    return result


def _load_goods_receipts(path: Path) -> dict[str, GoodsReceiptNote]:
    """Load GRs and index by po_number (one GR per PO in the dataset)."""
    records = _read_json(path)
    result: dict[str, GoodsReceiptNote] = {}
    for raw in records:
        try:
            gr = GoodsReceiptNote.model_validate(raw)
            result[gr.po_number] = gr
        except Exception as exc:
            logger.warning("Skipping invalid GR %s: %s", raw.get("gr_number"), exc)
    return result


def _load_exception_records(path: Path) -> dict[str, ExceptionRecord]:
    records = _read_json(path)
    result: dict[str, ExceptionRecord] = {}
    for raw in records:
        try:
            rec = ExceptionRecord.model_validate(raw)
            result[rec.exception_id] = rec
        except Exception as exc:
            logger.warning("Skipping invalid exception %s: %s", raw.get("exception_id"), exc)
    return result


def _load_suppliers(path: Path) -> dict[str, Supplier]:
    records = _read_json(path)
    result: dict[str, Supplier] = {}
    for raw in records:
        try:
            sup = Supplier.model_validate(raw)
            result[sup.supplier_id] = sup
        except Exception as exc:
            logger.warning("Skipping invalid supplier %s: %s", raw.get("supplier_id"), exc)
    return result


def _load_emails(path: Path) -> dict[str, Email]:
    records = _read_json(path)
    result: dict[str, Email] = {}
    for raw in records:
        try:
            email = Email.model_validate(raw)
            result[email.email_id] = email
        except Exception as exc:
            logger.warning("Skipping invalid email %s: %s", raw.get("email_id"), exc)
    return result


def _load_phone_transcripts(path: Path) -> dict[str, PhoneTranscript]:
    records = _read_json(path)
    result: dict[str, PhoneTranscript] = {}
    for raw in records:
        try:
            pt = PhoneTranscript.model_validate(raw)
            result[pt.transcript_id] = pt
        except Exception as exc:
            logger.warning(
                "Skipping invalid transcript %s: %s", raw.get("transcript_id"), exc
            )
    return result
