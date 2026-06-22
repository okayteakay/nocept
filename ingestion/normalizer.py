"""Unified LLM-based document normalizer for invoice, PO, and GRN ingestion.

Replaces sap_mapper.py + ocr.py + llm_extract.py. Accepts image|json|text|pdf
for invoice|po|grn and returns validated Pydantic models.

Key logic:
- Conforming JSON is validated directly (no LLM call).
- Non-conforming JSON, text, image, or PDF is passed to a vision-capable LLM
  for extraction and normalization.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Literal

from openai import OpenAI
from pydantic import ValidationError
from pdf2image import convert_from_bytes
from PIL import Image
import io

from config.settings import get_settings
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder

logger = logging.getLogger(__name__)

DocType = Literal["invoice", "po", "grn"]
DataFormat = Literal["json", "text", "image", "pdf"]

# Map doc types to their Pydantic models
DOC_MODELS = {
    "invoice": Invoice,
    "po": PurchaseOrder,
    "grn": GoodsReceiptNote,
}

# Prompts for LLM extraction (updated for actual model fields)
EXTRACTION_PROMPTS = {
    "invoice": """Extract the following fields from this invoice and return as JSON.
Fields required: invoice_number, po_number, supplier_id, supplier_name,
invoice_date (YYYY-MM-DD), due_date (YYYY-MM-DD), payment_terms, currency,
line_items (array of {sku, description, product_grade, quantity, unit_price, total}), total_amount.

If a field is missing, use reasonable defaults (e.g., currency=USD, payment_terms=Net 30, product_grade=Standard).
For line_items, estimate based on visible patterns.

Return only valid JSON with the above fields.""",
    "po": """Extract the following fields from this PO and return as JSON.
Fields required: po_number, supplier_id, supplier_name, created_by, creation_date (YYYY-MM-DD),
department, cost_center, currency, line_items (array of {sku, description, product_grade, quantity, unit_price, total}),
total_amount.

If a field is missing, use reasonable defaults (currency=USD, department=PROCUREMENT, cost_center=0000, product_grade=Standard).

Return only valid JSON with the above fields.""",
    "grn": """Extract the following fields from this GRN (Goods Receipt Note) and return as JSON.
Fields required: gr_number, po_number, invoice_number, supplier_id, date_received (YYYY-MM-DD),
received_by, line_items (array of {sku, description, product_grade, unit_price, quantity, total}), notes.

If a field is missing, use reasonable defaults (received_by=SYSTEM, notes=null, product_grade=Standard, unit_price=0, total=0).

Return only valid JSON with the above fields.""",
}


class NormalizerClient:
    """Unified document normalizer with JSON-first, LLM-fallback logic."""

    def __init__(self):
        cfg = get_settings()
        if not cfg.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set; cannot use normalizer")
        self.client = OpenAI(
            api_key=cfg.openai_api_key,
            timeout=cfg.openai_timeout_secs,
            max_retries=2,
            base_url=cfg.openai_base_url or None,
        )
        self.model = cfg.openai_model
        self.timeout = cfg.openai_timeout_secs

    def normalize(
        self,
        doc_type: DocType,
        data_format: DataFormat,
        data: bytes | str | dict,
    ) -> Invoice | PurchaseOrder | GoodsReceiptNote:
        """Normalize a document and return the validated model.

        Args:
            doc_type: "invoice", "po", or "grn"
            data_format: "json", "text", "image", or "pdf"
            data: Raw data (JSON string/dict, text string, image bytes, or PDF bytes)

        Returns:
            Validated Pydantic model instance

        Raises:
            ValueError: if validation fails after LLM extraction
            KeyError: if doc_type is unknown
        """
        model_class = DOC_MODELS[doc_type]

        # Fast path: conforming JSON
        if data_format == "json":
            parsed = self._parse_json(data)
            try:
                return model_class.model_validate(parsed)
            except ValidationError:
                logger.info(
                    f"JSON validation failed for {doc_type}; "
                    f"falling back to LLM normalization"
                )
                extracted_data = self._extract_with_llm(doc_type, parsed)
            except Exception as e:
                logger.error(f"JSON parsing failed: {e}")
                raise
        # LLM path: text, image, PDF
        elif data_format == "text":
            extracted_data = self._extract_with_llm(doc_type, data)
        elif data_format == "image":
            extracted_data = self._extract_from_image(doc_type, data)
        elif data_format == "pdf":
            extracted_data = self._extract_from_pdf(doc_type, data)
        else:
            raise ValueError(f"Unknown data format: {data_format}")

        try:
            return model_class.model_validate(extracted_data)
        except ValidationError as e:
            logger.error(f"Validation failed after LLM extraction: {e}")
            raise ValueError(
                f"LLM extraction resulted in invalid {doc_type} data"
            ) from e

    def _parse_json(self, data: bytes | str | dict) -> dict:
        """Parse JSON from bytes, string, or dict."""
        if isinstance(data, dict):
            return data
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    def _extract_with_llm(self, doc_type: DocType, text_or_data) -> dict:
        """Send text or non-conforming JSON to LLM for extraction."""
        if isinstance(text_or_data, dict):
            # Non-conforming JSON — serialize it
            text_content = json.dumps(text_or_data, indent=2)
        else:
            text_content = text_or_data

        prompt = EXTRACTION_PROMPTS[doc_type] + f"\n\nDocument:\n---\n{text_content}\n---"

        return self._call_llm_text(prompt)

    def _extract_from_image(self, doc_type: DocType, image_bytes: bytes) -> dict:
        """Extract from image using vision-capable LLM."""
        # Convert bytes to base64
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Infer MIME type (assume JPEG if ambiguous)
        mime_type = "image/jpeg"  # Could be enhanced with magic bytes detection
        image_url = f"data:{mime_type};base64,{b64_image}"

        prompt = EXTRACTION_PROMPTS[doc_type]

        return self._call_llm_vision(prompt, image_url)

    def _extract_from_pdf(self, doc_type: DocType, pdf_bytes: bytes) -> dict:
        """Convert PDF to images and extract using vision LLM."""
        try:
            images = convert_from_bytes(pdf_bytes, dpi=200)
            logger.info(f"PDF converted to {len(images)} page(s)")

            # For simplicity, use first page only (or concatenate all)
            if not images:
                raise ValueError("PDF has no pages")

            # Convert PIL Image to bytes
            img_byte_arr = io.BytesIO()
            images[0].save(img_byte_arr, format="JPEG")
            img_byte_arr.seek(0)
            image_bytes = img_byte_arr.getvalue()

            return self._extract_from_image(doc_type, image_bytes)

        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            raise ValueError(f"Failed to extract from PDF: {e}") from e

    def _call_llm_text(self, prompt: str) -> dict:
        """Call LLM with text prompt."""
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            timeout=self.timeout,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise document parser. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return self._parse_llm_response(response.choices[0].message.content or "")

    def _call_llm_vision(self, prompt: str, image_url: str) -> dict:
        """Call vision-capable LLM with image."""
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            timeout=self.timeout,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise document parser. Return only valid JSON.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
        return self._parse_llm_response(response.choices[0].message.content or "")

    def _parse_llm_response(self, raw: str) -> dict:
        """Strip code fences and parse JSON from LLM response."""
        raw = raw.strip()
        # Strip code fences if present
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)


def normalize_document(
    doc_type: DocType,
    data_format: DataFormat,
    data: bytes | str | dict,
) -> Invoice | PurchaseOrder | GoodsReceiptNote:
    """Convenience function: normalize a document without instantiating client.

    Args:
        doc_type: "invoice", "po", or "grn"
        data_format: "json", "text", "image", or "pdf"
        data: Raw data

    Returns:
        Validated Pydantic model instance
    """
    client = NormalizerClient()
    return client.normalize(doc_type, data_format, data)
