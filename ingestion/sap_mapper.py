"""Map SAP S/4HANA webhook payloads to internal Pydantic models.

SAP uses different field names (MM module) than our internal models.
These mappers handle the translation from SAP IDoc/BAPI notation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from models.invoice import Invoice, LineItem as InvoiceLineItem
from models.purchase_order import PurchaseOrder, LineItem as POLineItem
from models.grn import GoodsReceiptNote, LineItem as GRNLineItem


def map_sap_line_item(item: dict) -> InvoiceLineItem:
    """Map a SAP line item (E1EDP01 in INVOIC IDoc) to LineItem.

    SAP field names:
        MATNR: material number (SKU)
        ARKTX: short text
        MENGE: quantity
        MEINS: unit of measure
        NETPR: net price per unit
        MWSTS: tax amount (line)
        NETWR: net line amount
    """
    sku = item.get("MATNR", "").strip()
    if not sku:
        sku = item.get("material_number", "UNKNOWN")

    description = item.get("ARKTX", "")
    if not description:
        description = item.get("description", "")

    quantity = int(item.get("MENGE", item.get("quantity", 0)))
    unit_price = float(item.get("NETPR", item.get("unit_price", 0)))
    total = float(item.get("NETWR", item.get("total", quantity * unit_price)))

    return InvoiceLineItem(
        sku=sku,
        description=description,
        product_grade="standard",  # SAP doesn't have this; default to standard
        unit_price=unit_price,
        quantity=quantity,
        total=total,
    )


def map_sap_invoice(payload: dict) -> Invoice:
    """Map SAP INVOIC IDoc payload to Invoice model.

    Expected SAP field names (from MM/MM-PUR module):
        BELNR or VBELN: invoice number
        EBELN: PO number (EKKO.EBELN)
        LIFNR: supplier ID (from EKKO)
        LIFNM: supplier name (from EKKO)
        BLDAT: invoice date (document date)
        ZFBDT: due date (from payment terms or calculated)
        ZTERM: payment terms (e.g., "NET30", "NET45")
        WAERS: currency code (default USD)
        WRBTR: total amount (invoice total)
        E1EDP01: list of line items (structure repeats)
            Each item has MATNR, ARKTX, MENGE, NETPR, NETWR

    Modern SAP (REST/OData) may use different casing or formats.
    This mapper is lenient and accepts both old IDoc and modern naming.
    """
    # Invoice and PO identifiers
    invoice_number = payload.get("BELNR") or payload.get(
        "vbeln"
    ) or payload.get("invoice_number", "")
    po_number = payload.get("EBELN") or payload.get("po_number", "")
    supplier_id = payload.get("LIFNR") or payload.get("supplier_id", "")
    supplier_name = payload.get("LIFNM") or payload.get("supplier_name", "")

    # Dates
    invoice_date_str = payload.get("BLDAT") or payload.get("invoice_date")
    if isinstance(invoice_date_str, str):
        invoice_date = datetime.fromisoformat(invoice_date_str).date()
    else:
        invoice_date = invoice_date_str if isinstance(invoice_date_str, date) else date.today()

    due_date_str = payload.get("ZFBDT") or payload.get("due_date")
    if due_date_str:
        if isinstance(due_date_str, str):
            due_date = datetime.fromisoformat(due_date_str).date()
        else:
            due_date = due_date_str if isinstance(due_date_str, date) else date.today()
    else:
        # Default: Net 30 from invoice date
        due_date = invoice_date + timedelta(days=30)

    # Payment terms
    payment_terms = payload.get("ZTERM") or payload.get("payment_terms", "Net 30")

    # Currency (default USD)
    currency = payload.get("WAERS") or payload.get("currency", "USD")

    # Line items
    line_items_raw = payload.get("line_items", [])
    if isinstance(line_items_raw, dict) and "E1EDP01" in line_items_raw:
        # IDoc format: nested structure
        line_items_raw = line_items_raw["E1EDP01"]
    if not isinstance(line_items_raw, list):
        line_items_raw = [line_items_raw] if line_items_raw else []

    line_items = [map_sap_line_item(item) for item in line_items_raw]

    # Total amount
    total_amount = float(
        payload.get("WRBTR") or payload.get("total_amount", sum(li.total for li in line_items))
    )

    return Invoice(
        invoice_number=invoice_number,
        po_number=po_number,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=line_items,
        total_amount=total_amount,
        invoice_date=invoice_date,
        due_date=due_date,
        payment_terms=payment_terms,
        currency=currency,
    )


def map_sap_po(payload: dict) -> PurchaseOrder:
    """Map SAP EKKO/EKPO (PO header/line) payload to PurchaseOrder model.

    Expected SAP field names (from MM/MM-PUR module):
        EBELN: PO number
        LIFNR: supplier ID
        LIFNM: supplier name
        ERNAM: created by (user name)
        ERDAT: creation date
        ZTERM: payment terms
        WAERS: currency code
        NETWR: PO total net amount
        EKPO: list of line items
            Each item has MATNR, ARKTX, MENGE, MEINS, NETPR, NETWR
    """
    po_number = payload.get("EBELN") or payload.get("po_number", "")
    supplier_id = payload.get("LIFNR") or payload.get("supplier_id", "")
    supplier_name = payload.get("LIFNM") or payload.get("supplier_name", "")
    created_by = payload.get("ERNAM") or payload.get("created_by", "SAP_SYSTEM")

    # Creation date
    created_date_str = payload.get("ERDAT") or payload.get("creation_date")
    if isinstance(created_date_str, str):
        created_date = datetime.fromisoformat(created_date_str).date()
    else:
        created_date = created_date_str if isinstance(created_date_str, date) else date.today()

    # Default department/cost center (not in SAP EKKO directly, but extracted from header)
    department = payload.get("department", "PROCUREMENT")
    cost_center = payload.get("cost_center", "0000")

    # Currency
    currency = payload.get("WAERS") or payload.get("currency", "USD")

    # Line items
    line_items_raw = payload.get("line_items", [])
    if isinstance(line_items_raw, dict) and "EKPO" in line_items_raw:
        line_items_raw = line_items_raw["EKPO"]
    if not isinstance(line_items_raw, list):
        line_items_raw = [line_items_raw] if line_items_raw else []

    line_items = [map_sap_line_item(item) for item in line_items_raw]

    # Total amount
    total_amount = float(
        payload.get("NETWR") or payload.get("total_amount", sum(li.total for li in line_items))
    )

    return PurchaseOrder(
        po_number=po_number,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        line_items=line_items,
        total_amount=total_amount,
        creation_date=created_date,
        created_by=created_by,
        department=department,
        cost_center=cost_center,
        currency=currency,
    )


def map_sap_grn(payload: dict) -> GoodsReceiptNote:
    """Map SAP MBLNR/MSEG (GRN header/line) payload to GoodsReceiptNote model.

    Expected SAP field names (from MM/MM-WM module):
        MBLNR: GRN number (material document)
        MJAHR: fiscal year (combined with MBLNR for unique key)
        EBELN: PO number (reference)
        LIFNR: supplier ID
        BUDAT: posting date (goods receipt date)
        USNAM: user name (received by)
        MSEG: list of line items
            Each item has MATNR, MENGE, MEINS, CHARG, USNAM
    """
    gr_number = payload.get("MBLNR") or payload.get("gr_number", "")
    mjahr = payload.get("MJAHR", "")
    if mjahr and "/" not in gr_number:
        gr_number = f"{gr_number}/{mjahr}"

    po_number = payload.get("EBELN") or payload.get("po_number", "")
    supplier_id = payload.get("LIFNR") or payload.get("supplier_id", "")
    invoice_number = payload.get("invoice_number", "")

    # GRN date
    receipt_date_str = payload.get("BUDAT") or payload.get("date_received")
    if isinstance(receipt_date_str, str):
        receipt_date = datetime.fromisoformat(receipt_date_str).date()
    else:
        receipt_date = receipt_date_str if isinstance(receipt_date_str, date) else date.today()

    # Received by
    received_by = payload.get("USNAM") or payload.get("received_by", "SAP_SYSTEM")

    # Notes
    notes = payload.get("notes")

    # Line items (GRN items)
    line_items_raw = payload.get("line_items", [])
    if isinstance(line_items_raw, dict) and "MSEG" in line_items_raw:
        line_items_raw = line_items_raw["MSEG"]
    if not isinstance(line_items_raw, list):
        line_items_raw = [line_items_raw] if line_items_raw else []

    line_items = [map_sap_line_item(item) for item in line_items_raw]

    return GoodsReceiptNote(
        gr_number=gr_number,
        po_number=po_number,
        invoice_number=invoice_number,
        supplier_id=supplier_id,
        line_items=line_items,
        date_received=receipt_date,
        received_by=received_by,
        notes=notes,
    )
