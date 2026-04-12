"""
agent/comms_checker.py

Step 4 — Communications Confirmation Check.

Searches the emails and phone transcripts linked to the current exception
for language that directly confirms the exception (price change, substitution,
short delivery, etc.).

Scoring
-------
Each keyword hit from the type-specific keyword list adds 0.15 to the
confidence score (capped at 1.0).  PO/invoice number mentions add 0.20 each;
supplier name mention adds 0.10.

Auto-approve threshold: confidence >= 0.75

Note: Redis integration for emails/transcripts is a known work-in-progress.
The current check reads from the InvoiceException's related_emails and
related_transcripts fields (pre-loaded by the ingestion pipeline).
Once the Redis stream issue is resolved, this module will be updated to
also query Redis for any dynamically ingested communications.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from models.communication import Email, PhoneTranscript
from models.exception import InvoiceException
from models.exception_record import ExceptionType

logger = logging.getLogger(__name__)

# Confidence threshold to trigger auto-approval via communications
COMMS_CONFIRMATION_THRESHOLD = 0.75

# Keywords that strongly confirm each exception type
_KEYWORDS_BY_TYPE: dict[str, list[str]] = {
    ExceptionType.PRICE_VARIANCE.value: [
        "price increase",
        "price adjustment",
        "price change",
        "rate change",
        "unit price",
        "cost increase",
        "pricing update",
        "price escalation",
        "revised pricing",
        "new pricing",
        "cost adjustment",
        "updated rate",
    ],
    ExceptionType.QUANTITY_VARIANCE.value: [
        "partial delivery",
        "partial shipment",
        "quantity adjustment",
        "short ship",
        "backorder",
        "partial fulfillment",
        "quantity change",
        "units available",
        "short-shipped",
        "delivered partial",
        "unable to fulfill full",
        "remaining units",
    ],
    ExceptionType.INFORMAL_MODIFICATION.value: [
        "substitut",
        "replacement",
        "equivalent",
        "alternative",
        "discontinued",
        "out of stock",
        "no longer available",
        "modified order",
        "different product",
        "changed item",
        "grade change",
        "product change",
        "product swap",
        "next available",
        "nearest equivalent",
        "reformulat",
    ],
    ExceptionType.MISSING_GOODS_RECEIPT.value: [
        "delivery confirm",
        "shipment received",
        "goods received",
        "delivery note",
        "receipt confirm",
        "delivered on",
        "package arrived",
        "received your shipment",
        "grn",
        "goods receipt",
    ],
    ExceptionType.DUPLICATE_INVOICE.value: [],  # Duplicates are always rejected
    ExceptionType.NONE.value: [],
}


@dataclass
class CommsConfirmation:
    """Result of a single communication analysis."""

    confirmed: bool
    confidence: float
    source_type: str        # "email" or "transcript"
    source_id: str
    excerpt: str            # Most relevant snippet from the communication
    matched_keywords: list[str]
    reasoning: str


class CommsCheckResult:
    """Aggregated output of the full communications check."""

    def __init__(
        self,
        auto_approve: bool,
        best_confirmation: Optional[CommsConfirmation],
        total_checked: int,
        reasoning: str,
    ) -> None:
        self.auto_approve = auto_approve
        self.best_confirmation = best_confirmation
        self.total_checked = total_checked
        self.reasoning = reasoning


def check_communications(exception: InvoiceException) -> CommsCheckResult:
    """
    Search all emails and transcripts linked to the exception for direct
    confirmation of the exception cause.

    Parameters
    ----------
    exception:
        The current InvoiceException, with related_emails and
        related_transcripts already populated from the dataset.

    Returns
    -------
    CommsCheckResult
    """
    total_checked = len(exception.related_emails) + len(exception.related_transcripts)

    if total_checked == 0:
        return CommsCheckResult(
            auto_approve=False,
            best_confirmation=None,
            total_checked=0,
            reasoning=(
                "No emails or transcripts are linked to this exception. "
                "Cannot confirm via communications."
            ),
        )

    best_conf: Optional[CommsConfirmation] = None
    best_confidence = 0.0

    for email in exception.related_emails:
        result = _analyse_email(email, exception)
        if result and result.confidence > best_confidence:
            best_confidence = result.confidence
            best_conf = result

    for transcript in exception.related_transcripts:
        result = _analyse_transcript(transcript, exception)
        if result and result.confidence > best_confidence:
            best_confidence = result.confidence
            best_conf = result

    should_approve = best_confidence >= COMMS_CONFIRMATION_THRESHOLD

    if best_conf is None:
        reasoning = (
            f"Checked {total_checked} communication(s) but found no relevant content. "
            "Cannot confirm via communications."
        )
    elif should_approve:
        reasoning = (
            f"Communication {best_conf.source_id} directly confirms this exception "
            f"(confidence {best_confidence:.2f}). "
            f"Source type: {best_conf.source_type}. "
            f"Matched terms: {', '.join(best_conf.matched_keywords[:4])}. "
            f"Excerpt: \"{best_conf.excerpt[:250]}\". "
            "Auto-approving based on communication evidence."
        )
    else:
        reasoning = (
            f"Checked {total_checked} communication(s). "
            f"Best confidence: {best_confidence:.2f} "
            f"(threshold: {COMMS_CONFIRMATION_THRESHOLD}). "
            "Communications do not sufficiently confirm this exception."
        )

    return CommsCheckResult(
        auto_approve=should_approve,
        best_confirmation=best_conf,
        total_checked=total_checked,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _analyse_email(
    email: Email, exception: InvoiceException
) -> Optional[CommsConfirmation]:
    full_text = f"{email.subject} {email.body}".lower()
    return _score_text(
        text=full_text,
        raw_text=email.body,
        source_type="email",
        source_id=email.email_id,
        exception=exception,
    )


def _analyse_transcript(
    transcript: PhoneTranscript, exception: InvoiceException
) -> Optional[CommsConfirmation]:
    full_text = transcript.transcript.lower()
    return _score_text(
        text=full_text,
        raw_text=transcript.transcript,
        source_type="transcript",
        source_id=transcript.transcript_id,
        exception=exception,
    )


def _score_text(
    text: str,
    raw_text: str,
    source_type: str,
    source_id: str,
    exception: InvoiceException,
) -> Optional[CommsConfirmation]:
    """Score a lowercased communication text against the current exception."""
    score = 0.0
    matched: list[str] = []

    for exc_type in exception.exception_types:
        for kw in _KEYWORDS_BY_TYPE.get(exc_type.value, []):
            if kw.lower() in text and kw not in matched:
                score += 0.15
                matched.append(kw)

    # Reference matches (strong signal — supplier/PO/invoice mentioned)
    if exception.purchase_order.po_number.lower() in text:
        score += 0.20
    if exception.invoice.invoice_number.lower() in text:
        score += 0.20
    if exception.invoice.supplier_name.lower() in text:
        score += 0.10

    score = min(1.0, score)

    if score <= 0:
        return None

    excerpt = _best_excerpt(raw_text, matched)

    return CommsConfirmation(
        confirmed=score >= COMMS_CONFIRMATION_THRESHOLD,
        confidence=score,
        source_type=source_type,
        source_id=source_id,
        excerpt=excerpt,
        matched_keywords=matched,
        reasoning=f"Matched {len(matched)} keyword(s): {', '.join(matched[:5])}",
    )


def _best_excerpt(text: str, keywords: list[str]) -> str:
    """Return a ~300-char excerpt centred on the first matched keyword."""
    if not keywords:
        return text[:300]
    for kw in keywords:
        idx = text.lower().find(kw.lower())
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(text), idx + 220)
            return text[start:end]
    return text[:300]
