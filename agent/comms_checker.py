"""
agent/comms_checker.py

Step 4 — Communications Confirmation Check.

Searches emails and phone transcripts linked to the current exception and uses
an OpenAI-compatible LLM to decide whether the communication directly confirms
the exception and justifies auto-approval.

Flow
----
1. Collect all pre-linked emails and transcripts from the InvoiceException.
2. For each communication, send the exception context + full comm text to the
   LLM and ask for a structured YES/NO approval decision.
3. Auto-approve if any communication yields confidence >= COMMS_CONFIRMATION_THRESHOLD.

Failure mode: if the LLM is unavailable, returns ``auto_approve=False`` so the
exception is escalated to a human reviewer. There is no keyword-based fallback
because it is brittle and silently disagrees with the LLM's verdict.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from config.settings import get_settings
from models.communication import Email, PhoneTranscript
from models.exception import InvoiceException

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
    """Search all emails and transcripts linked to the exception.

    Uses an OpenAI-compatible LLM to read each communication and decide
    whether it directly confirms the exception cause. If the LLM is
    unavailable, returns ``auto_approve=False`` so the exception is escalated.
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

    try:
        client = _get_client()
    except Exception as e:
        logger.error(f"Failed to initialize LLM client: {e}. Escalating.", exc_info=True)
        return CommsCheckResult(
            auto_approve=False,
            best_confirmation=None,
            total_checked=total_checked,
            reasoning="LLM unavailable; communications check cannot confirm this exception.",
        )

    best_conf: Optional[CommsConfirmation] = None
    best_confidence = 0.0
    failures = 0

    for email in exception.related_emails:
        result = _analyse_with_llm(
            text=f"Subject: {email.subject}\n\n{email.body}",
            source_type="email",
            source_id=email.email_id,
            exception=exception,
            client=client,
        )
        if result is None:
            failures += 1
            continue
        if result.confidence > best_confidence:
            best_confidence = result.confidence
            best_conf = result

    for transcript in exception.related_transcripts:
        result = _analyse_with_llm(
            text=transcript.transcript,
            source_type="transcript",
            source_id=transcript.transcript_id,
            exception=exception,
            client=client,
        )
        if result is None:
            failures += 1
            continue
        if result.confidence > best_confidence:
            best_confidence = result.confidence
            best_conf = result

    should_approve = best_confidence >= COMMS_CONFIRMATION_THRESHOLD

    if best_conf is None:
        reasoning = (
            f"Checked {total_checked} communication(s) but the LLM failed on all "
            f"({failures} failure(s)). Cannot confirm via communications; "
            "escalating for human review."
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


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _get_client() -> OpenAI:
    """Initialize OpenAI client with timeout and retry configuration."""
    cfg = get_settings()

    if not cfg.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    kwargs: dict = {
        "api_key": cfg.openai_api_key,
        "timeout": cfg.openai_timeout_secs,
        "max_retries": 2,
    }
    if cfg.openai_base_url:
        kwargs["base_url"] = cfg.openai_base_url
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
    client: OpenAI,
) -> Optional[CommsConfirmation]:
    """Call an OpenAI-compatible model to assess whether the communication confirms the exception.

    Returns ``None`` on any failure (LLM error, timeout, malformed response).
    The caller treats ``None`` as "no evidence found" and decides accordingly.
    """
    cfg = get_settings()

    try:
        response = client.chat.completions.create(
            model=cfg.openai_model,
            max_tokens=256,
            timeout=cfg.openai_timeout_secs,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(exception, text)},
            ],
        )
        raw = response.choices[0].message.content
        if not raw:
            logger.warning(f"Empty LLM response for {source_type} {source_id}")
            return None

        parsed = json.loads(raw.strip())
        confidence = float(parsed.get("confidence", 0.0))
        return CommsConfirmation(
            confirmed=bool(parsed.get("confirms", False)),
            confidence=confidence,
            source_type=source_type,
            source_id=source_id,
            excerpt=text[:300],
        )
    except (json.JSONDecodeError, TimeoutError, Exception) as exc:
        logger.warning(
            f"LLM comms check failed for {source_type} {source_id}: {type(exc).__name__}: {exc}"
        )
        return None
