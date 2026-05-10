"""OCR and document parsing for PDF invoices, POs, and GRNs.

Uses Tesseract OCR + pdf2image for extraction, then LLM for structured parsing.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime

from pdf2image import convert_from_bytes
import pytesseract

from models.invoice import Invoice, LineItem as InvoiceLineItem
from models.purchase_order import PurchaseOrder, LineItem as POLineItem
from models.grn import GoodsReceiptNote, LineItem as GRNLineItem

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF using OCR.

    Converts PDF to images using pdf2image, then runs Tesseract OCR on each page.

    Args:
        pdf_bytes: Raw PDF bytes

    Returns:
        Concatenated text from all pages

    Raises:
        Exception: if OCR fails or Tesseract not installed
    """
    logger.info("Starting OCR extraction from PDF")

    try:
        # Convert PDF pages to images
        images = convert_from_bytes(pdf_bytes, dpi=200)
        logger.info(f"PDF converted to {len(images)} image(s)")

        # OCR each image
        text_parts = []
        for i, image in enumerate(images):
            logger.info(f"Running OCR on page {i+1}/{len(images)}")
            text = pytesseract.image_to_string(image)
            text_parts.append(text)

        # Join pages with page break markers
        full_text = "\n\n---PAGE BREAK---\n\n".join(text_parts)
        logger.info(f"OCR extraction complete; {len(full_text)} characters extracted")

        return full_text

    except FileNotFoundError as e:
        logger.error(f"Tesseract not installed or PDF extraction failed: {e}")
        raise Exception(
            "Tesseract OCR not installed. Please install tesseract-ocr package."
        ) from e
    except Exception as e:
        logger.error(f"PDF OCR extraction failed: {e}", exc_info=True)
        raise


def parse_invoice_from_text(
    text: str,
    llm_extract_fn,
) -> Invoice:
    """Parse OCR-extracted text into an Invoice model using LLM.

    Sends the extracted text to an LLM (e.g., OpenAI GPT-4o-mini) with
    structured output instructions to extract invoice fields.

    Args:
        text: Extracted text from OCR
        llm_extract_fn: Function that takes prompt and returns dict with
                       invoice fields (invoice_number, po_number, etc.)

    Returns:
        Parsed Invoice model

    Raises:
        ValueError: if LLM fails to parse or returned data is invalid
    """
    logger.info("Parsing invoice from OCR text using LLM")

    prompt = f"""Extract the following fields from this invoice OCR text and return as JSON.
Fields required: invoice_number, po_number, supplier_id, supplier_name,
invoice_date (YYYY-MM-DD), due_date (YYYY-MM-DD), payment_terms, currency,
line_items (array of {{sku, description, quantity, unit_price, total}}), total_amount.

If a field is missing, use reasonable defaults (e.g., currency=USD, payment_terms=Net 30).
For line_items, estimate based on visible patterns in the text.

OCR Text:
---
{text}
---

Return only valid JSON with the above fields."""

    try:
        parsed_data = llm_extract_fn(prompt)
        invoice = Invoice(**parsed_data)
        logger.info(f"Successfully parsed invoice: {invoice.invoice_number}")
        return invoice

    except Exception as e:
        logger.error(f"LLM invoice parsing failed: {e}")
        raise ValueError(f"Failed to parse invoice from OCR text: {e}") from e


def parse_po_from_text(
    text: str,
    llm_extract_fn,
) -> PurchaseOrder:
    """Parse OCR-extracted text into a PurchaseOrder model using LLM.

    Args:
        text: Extracted text from OCR
        llm_extract_fn: Function that takes prompt and returns dict with PO fields

    Returns:
        Parsed PurchaseOrder model

    Raises:
        ValueError: if LLM fails to parse or returned data is invalid
    """
    logger.info("Parsing PO from OCR text using LLM")

    prompt = f"""Extract the following fields from this PO OCR text and return as JSON.
Fields required: po_number, supplier_id, supplier_name, created_by, created_date (YYYY-MM-DD),
department, cost_center, currency, line_items (array of {{sku, description, quantity, unit_price, total}}),
total_amount.

If a field is missing, use reasonable defaults (currency=USD, department=PROCUREMENT, cost_center=0000).

OCR Text:
---
{text}
---

Return only valid JSON with the above fields."""

    try:
        parsed_data = llm_extract_fn(prompt)
        po = PurchaseOrder(**parsed_data)
        logger.info(f"Successfully parsed PO: {po.po_number}")
        return po

    except Exception as e:
        logger.error(f"LLM PO parsing failed: {e}")
        raise ValueError(f"Failed to parse PO from OCR text: {e}") from e


def parse_grn_from_text(
    text: str,
    llm_extract_fn,
) -> GoodsReceiptNote:
    """Parse OCR-extracted text into a GoodsReceiptNote model using LLM.

    Args:
        text: Extracted text from OCR
        llm_extract_fn: Function that takes prompt and returns dict with GRN fields

    Returns:
        Parsed GoodsReceiptNote model

    Raises:
        ValueError: if LLM fails to parse or returned data is invalid
    """
    logger.info("Parsing GRN from OCR text using LLM")

    prompt = f"""Extract the following fields from this GRN (Goods Receipt Note) OCR text and return as JSON.
Fields required: gr_number, po_number, invoice_number, supplier_id, date_received (YYYY-MM-DD),
received_by, line_items (array of {{sku, description, quantity}}), notes.

If a field is missing, use reasonable defaults (received_by=SYSTEM, notes=null).

OCR Text:
---
{text}
---

Return only valid JSON with the above fields."""

    try:
        parsed_data = llm_extract_fn(prompt)
        grn = GoodsReceiptNote(**parsed_data)
        logger.info(f"Successfully parsed GRN: {grn.gr_number}")
        return grn

    except Exception as e:
        logger.error(f"LLM GRN parsing failed: {e}")
        raise ValueError(f"Failed to parse GRN from OCR text: {e}") from e
