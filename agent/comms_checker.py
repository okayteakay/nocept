"""
agent/comms_checker.py

Step 4 — Communications Confirmation Check.

Searches emails and phone transcripts linked to the current exception and uses
an OpenAI-compatible LLM (via nanogpt) to decide whether the communication
directly confirms the exception and justifies auto-approval.

Flow
----
1. Collect all pre-linked emails and transcripts from the InvoiceException.
2. For each communication, send the exception context + full comm text to Claude
   and ask for a structured YES/NO approval decision.
3. Auto-approve if any communication yields confidence >= COMMS_CONFIRMATION_THRESHOLD.

Note: Redis-based search for unlinked comms (by supplier/buyer pair) is a
known work-in-progress; currently only pre-linked communications are checked.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from models.communication import Email, PhoneTranscript
from models.exception import InvoiceException
from models.exception_record import ExceptionType

logger = logging.getLogger(__name__)

COMMS_CONFIRMATION_THRESHOLD = 0.75

_SYSTEM_PROMPT = """You are an Accounts Payable analyst reviewing invoice exceptions.
Given an invoice exception and a related communication (email or phone transcript),
determine whether the communication directly confirms or explains the exception
well enough to justify automatic approval.

Respond with valid JSON only — no markdown, no prose outside the JSON object:
{
  "confirms": true | false,
  "confidence": <float 0.0–1.0>
}

Guidelines:
- "confirms" should be true only when the communication clearly addresses the
  specific discrepancy described in the exception (price change, substitution,
  short/over delivery, delivery confirmation, etc.).
- "confidence" reflects how directly and explicitly the communication speaks to
  the exception.  A vague or tangentially related message scores low (< 0.5).
  An explicit, on-point confirmation scores high (>= 0.75).
- Do NOT approve based on general relationship-building messages, payment
  reminders, or delivery notifications that do not address the specific variance.
"""


@dataclass
class CommsConfirmation:
    """Result of a single communication analysis."""

    confirmed: bool
    confidence: float
    source_type: str      # "email" or "transcript"
    source_id: str
    excerpt: str          # Most relevant snippet from the communication


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
    Search all emails and transcripts linked to the exception.

    Uses an OpenAI-compatible LLM to read each communication and decide
    whether it directly confirms the exception cause. Gracefully falls back
    to keyword analysis if LLM is unavailable.
    """
    try:
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

        try:
            client = _get_client()
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}. Will use keyword fallback only.", exc_info=True)
            client = None

        best_conf: Optional[CommsConfirmation] = None
        best_confidence = 0.0

        for email in exception.related_emails:
            try:
                result = _analyse_with_llm(
                    text=f"Subject: {email.subject}\n\n{email.body}",
                    source_type="email",
                    source_id=email.email_id,
                    exception=exception,
                    client=client,
                )
                if result and result.confidence > best_confidence:
                    best_confidence = result.confidence
                    best_conf = result
            except Exception as e:
                logger.warning(f"Error analyzing email {email.email_id}: {e}")
                continue

        for transcript in exception.related_transcripts:
            try:
                result = _analyse_with_llm(
                    text=transcript.transcript,
                    source_type="transcript",
                    source_id=transcript.transcript_id,
                    exception=exception,
                    client=client,
                )
                if result and result.confidence > best_confidence:
                    best_confidence = result.confidence
                    best_conf = result
            except Exception as e:
                logger.warning(f"Error analyzing transcript {transcript.transcript_id}: {e}")
                continue

        should_approve = best_confidence >= COMMS_CONFIRMATION_THRESHOLD

        if best_conf is None:
            reasoning = (
                f"Checked {total_checked} communication(s) but no relevant content found. "
                "Cannot confirm via communications."
            )
        elif should_approve:
            reasoning = (
                f"Communication {best_conf.source_id} directly confirms this exception "
                f"(confidence {best_confidence:.2f}). "
                f"Source type: {best_conf.source_type}. "
                f'Excerpt: "{best_conf.excerpt[:250]}". '
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
    except Exception as e:
        logger.error(f"Unexpected error in check_communications: {e}", exc_info=True)
        return CommsCheckResult(
            auto_approve=False,
            best_confirmation=None,
            total_checked=0,
            reasoning="Error during communication check. See logs for details.",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _get_client() -> OpenAI:
    """Initialize OpenAI client with timeout and retry configuration."""
    api_key  = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    timeout = float(os.environ.get("OPENAI_TIMEOUT_SECS", "30"))

    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    kwargs: dict = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 2,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _build_user_prompt(exception: InvoiceException, comm_text: str) -> str:
    exc_types = ", ".join(t.value for t in exception.exception_types)
    variance_pct = (
        float(exception.invoice.total_amount - exception.purchase_order.total_amount)
        / float(exception.purchase_order.total_amount)
        * 100
        if float(exception.purchase_order.total_amount) > 0
        else 0.0
    )
    return (
        f"Exception details:\n"
        f"  Supplier: {exception.invoice.supplier_name}\n"
        f"  Exception type(s): {exc_types}\n"
        f"  PO number: {exception.purchase_order.po_number}\n"
        f"  Invoice number: {exception.invoice.invoice_number}\n"
        f"  PO total: ${float(exception.purchase_order.total_amount):,.2f}\n"
        f"  Invoice total: ${float(exception.invoice.total_amount):,.2f}\n"
        f"  Variance: {variance_pct:+.2f}%\n"
        f"  Total variance USD: ${exception.total_variance_usd:,.2f}\n\n"
        f"Communication text:\n{comm_text}"
    )


def _analyse_with_llm(
    text: str,
    source_type: str,
    source_id: str,
    exception: InvoiceException,
    client: Optional[OpenAI],
) -> Optional[CommsConfirmation]:
    """Call an OpenAI-compatible model to assess whether the communication confirms the exception.

    Falls back to keyword analysis if LLM is unavailable or times out.
    """
    if client is None:
        logger.debug(f"LLM client not available for {source_type} {source_id}, using keyword fallback")
        return _keyword_fallback(text, source_type, source_id, exception)

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    timeout = float(os.environ.get("OPENAI_TIMEOUT_SECS", "30"))

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=256,
            timeout=timeout,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(exception, text)},
            ],
        )
        raw = response.choices[0].message.content
        if not raw:
            logger.warning(f"Empty response from LLM for {source_type} {source_id}")
            return _keyword_fallback(text, source_type, source_id, exception)

        raw = raw.strip()
        parsed = json.loads(raw)
        confidence = float(parsed.get("confidence", 0.0))
        return CommsConfirmation(
            confirmed=bool(parsed.get("confirms", False)),
            confidence=confidence,
            source_type=source_type,
            source_id=source_id,
            excerpt=text[:300],
        )
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM JSON response for {source_type} {source_id}: {e}")
        return _keyword_fallback(text, source_type, source_id, exception)
    except TimeoutError as e:
        logger.warning(f"LLM request timed out for {source_type} {source_id} (>{timeout}s), using keyword fallback")
        return _keyword_fallback(text, source_type, source_id, exception)
    except Exception as exc:
        logger.warning(
            f"LLM comms check failed for {source_type} {source_id}: {exc} — falling back to keyword analysis"
        )
        return _keyword_fallback(text, source_type, source_id, exception)


def _keyword_fallback(
    text: str,
    source_type: str,
    source_id: str,
    exception: InvoiceException,
) -> Optional[CommsConfirmation]:
    """Rule-based fallback when the LLM endpoint is unavailable.

    Checks whether the communication mentions the PO number and contains
    keywords that match the exception type.  Returns a conservative confidence
    score so that only clearly relevant communications pass the threshold.
    """
    text_lower = text.lower()
    po = exception.purchase_order.po_number

    # Must reference the PO
    if po.lower() not in text_lower:
        return None

    # Exception-type keyword families
    _KEYWORD_SETS: dict[str, list[str]] = {
        "substitution": [
            "swap", "substitute", "substitut", "replace", "upgrade",
            "vented", "surgical", "instead", "fill the rest", "fill with",
        ],
        "price": [
            "price", "cost", "per unit", "/unit", "more per",
            "difference", "increase", "surcharge", "uplift",
        ],
        "shortage": [
            "short", "running low", "out of stock", "backorder",
            "production delay", "unavailable",
        ],
        "approval": [
            "approve", "go ahead", "absorb", "accept", "ok with",
            "alright", "agreed", "confirmed", "flexibility",
        ],
    }

    exc_types = exception.exception_types
    relevant_families: list[str] = []
    if ExceptionType.INFORMAL_MODIFICATION in exc_types:
        relevant_families = ["substitution", "price", "shortage", "approval"]
    elif ExceptionType.PRICE_VARIANCE in exc_types:
        relevant_families = ["price", "approval"]
    else:
        relevant_families = list(_KEYWORD_SETS.keys())

    # Score by family coverage: how many relevant categories have at least one hit
    families_hit = 0
    total_hits = 0
    for family in relevant_families:
        family_hits = sum(1 for kw in _KEYWORD_SETS[family] if kw in text_lower)
        if family_hits > 0:
            families_hit += 1
        total_hits += family_hits

    if families_hit == 0:
        return None

    # PO match + family coverage: 2+ families matched → strong signal
    family_ratio = families_hit / len(relevant_families)
    confidence = min(0.50 + family_ratio * 0.45, 0.95)

    logger.info(
        "Keyword fallback for %s %s: %d/%d families hit, %d keyword hits → confidence %.2f",
        source_type, source_id, families_hit, len(relevant_families), total_hits, confidence,
    )

    return CommsConfirmation(
        confirmed=confidence >= COMMS_CONFIRMATION_THRESHOLD,
        confidence=confidence,
        source_type=source_type,
        source_id=source_id,
        excerpt=text[:300],
    )
