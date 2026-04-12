"""knowledge/seeder.py

Startup seeder: loads all historical data from the JSON dataset into the three
knowledge-base stores on every application start (upsert semantics — safe to
run repeatedly without creating duplicates).

What gets seeded
----------------
1. **Resolution history** — every exception record that is marked as resolved
   or escalated in the dataset.  The dataset does not carry an explicit
   final_state (records are pre-classified, not yet run through the pipeline),
   so we synthesise a minimal Resolution for each exception record so that the
   agent has a rich historical pattern library from day one.

2. **Email vector index** — all 100 emails from emails.json, embedded and
   stored with full metadata.

3. **Transcript vector index** — all 40 phone transcripts from
   phone_transcripts.json, embedded and stored with full metadata.

As new cases are resolved at runtime, the caller is responsible for calling
``ingest_resolved_case()`` on the KnowledgeBaseClient, which writes to all
three stores incrementally.  The seeder is never the write-path for live data.

Idempotency strategy
--------------------
- Resolution records: the seeder calls ``resolution_store.upsert()`` which does
  an HSET (always overwrites the hash) and ZADD (NX flag not set, so it
  overwrites the score if the ID already exists).  Safe to re-run.
- Vector indexes: ``upsert_batch`` calls HSET on every document, overwriting
  any existing embedding.  Indexes are automatically kept in sync by Redis
  Stack as hashes change.  Safe to re-run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ingestion.json_ingestor import DatasetBundle
from knowledge.email_store import EmailVectorStore
from knowledge.resolution_store import ResolutionHistoryStore
from knowledge.transcript_store import TranscriptVectorStore
from models.exception import ExceptionState, InvoiceException
from models.exception_record import ExceptionRecord, ExceptionType
from models.grn import GoodsReceiptNote
from models.invoice import Invoice
from models.purchase_order import PurchaseOrder
from models.resolution import (
    EvidenceItem,
    Resolution,
    ResolutionAction,
    ResolutionMemo,
    RootCause,
)

logger = logging.getLogger(__name__)

# Mapping from dataset ExceptionType to the most appropriate RootCause for the
# synthesised resolution record.
_EXCEPTION_TYPE_TO_ROOT_CAUSE: dict[ExceptionType, RootCause] = {
    ExceptionType.NONE: RootCause.POLICY_COMPLIANT_VARIANCE,
    ExceptionType.PRICE_VARIANCE: RootCause.BILLING_ERROR,
    ExceptionType.QUANTITY_VARIANCE: RootCause.BILLING_ERROR,
    ExceptionType.MISSING_GOODS_RECEIPT: RootCause.SYSTEM_ERROR,
    ExceptionType.DUPLICATE_INVOICE: RootCause.DUPLICATE_SUBMISSION,
    ExceptionType.INFORMAL_MODIFICATION: RootCause.UNDOCUMENTED_MODIFICATION,
}

_EXCEPTION_TYPE_TO_ACTION: dict[ExceptionType, ResolutionAction] = {
    ExceptionType.NONE: ResolutionAction.AUTO_APPROVE,
    ExceptionType.PRICE_VARIANCE: ResolutionAction.REQUEST_CREDIT_NOTE,
    ExceptionType.QUANTITY_VARIANCE: ResolutionAction.REQUEST_CREDIT_NOTE,
    ExceptionType.MISSING_GOODS_RECEIPT: ResolutionAction.ESCALATE_TO_HUMAN,
    ExceptionType.DUPLICATE_INVOICE: ResolutionAction.AUTO_REJECT,
    ExceptionType.INFORMAL_MODIFICATION: ResolutionAction.ESCALATE_TO_HUMAN,
}

# Exceptions requiring human review are seeded as ESCALATED; others RESOLVED.
_ESCALATED_TYPES: set[ExceptionType] = {
    ExceptionType.INFORMAL_MODIFICATION,
    ExceptionType.MISSING_GOODS_RECEIPT,
}


def seed_knowledge_base(
    bundle: DatasetBundle,
    resolution_store: ResolutionHistoryStore,
    email_store: EmailVectorStore,
    transcript_store: TranscriptVectorStore,
) -> dict[str, int]:
    """Seed all three knowledge-base stores from the loaded DatasetBundle.

    Ensures vector indexes exist before writing, then upserts all records.

    Args:
        bundle: Loaded dataset bundle (invoices, POs, GRs, exceptions, emails, transcripts).
        resolution_store: Target resolution history store.
        email_store: Target email vector store.
        transcript_store: Target transcript vector store.

    Returns:
        Dict with counts: ``{'resolutions': N, 'emails': N, 'transcripts': N}``.
    """
    logger.info("Knowledge base seeding started…")

    # 1. Ensure vector indexes exist (idempotent)
    email_store.ensure_index()
    transcript_store.ensure_index()

    # 2. Seed resolution history from pre-computed exception records
    n_resolutions = _seed_resolutions(bundle, resolution_store)

    # 3. Seed emails
    n_emails = _seed_emails(bundle, email_store)

    # 4. Seed transcripts
    n_transcripts = _seed_transcripts(bundle, transcript_store)

    logger.info(
        "Knowledge base seeding complete — resolutions: %d, emails: %d, transcripts: %d",
        n_resolutions,
        n_emails,
        n_transcripts,
    )
    return {"resolutions": n_resolutions, "emails": n_emails, "transcripts": n_transcripts}


# ---------------------------------------------------------------------------
# Private seeders
# ---------------------------------------------------------------------------


def _seed_resolutions(
    bundle: DatasetBundle,
    store: ResolutionHistoryStore,
) -> int:
    """Synthesise InvoiceException + Resolution pairs and upsert to the history store."""
    count = 0
    for rec in bundle.exception_records.values():
        if not rec.is_exception:
            continue  # Skip clean invoices — they add no pattern signal.

        invoice = bundle.invoices.get(rec.invoice_number)
        po = bundle.purchase_orders.get(rec.po_number)
        if invoice is None or po is None:
            logger.warning(
                "Seeder: skipping %s — missing invoice %s or PO %s",
                rec.exception_id,
                rec.invoice_number,
                rec.po_number,
            )
            continue

        grn = bundle.goods_receipts.get(rec.po_number)
        exc, resolution = _synthesise_exception_and_resolution(rec, invoice, po, grn)

        try:
            store.upsert(exc, resolution)
            count += 1
        except Exception as err:
            logger.warning("Seeder: failed to upsert resolution for %s: %s", rec.exception_id, err)

    return count


def _seed_emails(bundle: DatasetBundle, store: EmailVectorStore) -> int:
    """Batch-upsert all emails from the dataset bundle."""
    emails = list(bundle.emails.values())
    if not emails:
        return 0
    try:
        store.upsert_batch(emails)
        return len(emails)
    except Exception as err:
        logger.error("Seeder: email batch upsert failed: %s", err)
        # Fall back to one-by-one so partial failures don't block everything
        count = 0
        for email in emails:
            try:
                store.upsert(email)
                count += 1
            except Exception as inner:
                logger.warning("Seeder: failed to upsert email %s: %s", email.email_id, inner)
        return count


def _seed_transcripts(bundle: DatasetBundle, store: TranscriptVectorStore) -> int:
    """Batch-upsert all phone transcripts from the dataset bundle."""
    transcripts = list(bundle.phone_transcripts.values())
    if not transcripts:
        return 0
    try:
        store.upsert_batch(transcripts)
        return len(transcripts)
    except Exception as err:
        logger.error("Seeder: transcript batch upsert failed: %s", err)
        count = 0
        for t in transcripts:
            try:
                store.upsert(t)
                count += 1
            except Exception as inner:
                logger.warning(
                    "Seeder: failed to upsert transcript %s: %s", t.transcript_id, inner
                )
        return count


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------


def _synthesise_exception_and_resolution(
    rec: ExceptionRecord,
    invoice: Invoice,
    po: PurchaseOrder,
    grn: GoodsReceiptNote | None,
) -> tuple[InvoiceException, Resolution]:
    """Build minimal InvoiceException + Resolution objects from a dataset record.

    The dataset records are pre-classified with the exception type and variance
    but have no pipeline-computed line variances.  We build a lightweight
    exception object sufficient for the history store (supplier/PO/invoice
    indexes) and attach a synthesised resolution memo.
    """
    final_state = (
        ExceptionState.ESCALATED
        if rec.exception_type in _ESCALATED_TYPES
        else ExceptionState.RESOLVED
    )

    exc = InvoiceException(
        exception_id=rec.exception_id,
        invoice=invoice,
        purchase_order=po,
        grn=grn,
        state=final_state,
        exception_types=[rec.exception_type],
        line_variances=[],
        total_variance_usd=float(rec.variance_amount),
    )

    root_cause = _EXCEPTION_TYPE_TO_ROOT_CAUSE.get(
        rec.exception_type, RootCause.UNRESOLVED
    )
    action = _EXCEPTION_TYPE_TO_ACTION.get(
        rec.exception_type, ResolutionAction.ESCALATE_TO_HUMAN
    )
    confidence = 0.85 if final_state == ExceptionState.RESOLVED else 0.60

    evidence: list[EvidenceItem] = []
    if rec.related_email_ids:
        evidence.append(
            EvidenceItem(
                source="dataset_email",
                description=f"Linked email(s): {', '.join(rec.related_email_ids)}",
                confidence=0.8,
            )
        )
    if rec.related_transcript_ids:
        evidence.append(
            EvidenceItem(
                source="dataset_transcript",
                description=f"Linked call(s): {', '.join(rec.related_transcript_ids)}",
                confidence=0.8,
            )
        )

    memo = ResolutionMemo(
        exception_id=rec.exception_id,
        root_cause=root_cause,
        action=action,
        confidence=confidence,
        summary=rec.description,
        evidence=evidence,
    )

    # Use a fixed seeding timestamp derived from invoice date for consistent ordering.
    resolved_at = datetime(
        invoice.invoice_date.year,
        invoice.invoice_date.month,
        invoice.invoice_date.day,
        tzinfo=timezone.utc,
    )

    resolution = Resolution(
        exception_id=rec.exception_id,
        memo=memo,
        resolved_at=resolved_at,
        resolved_by="seeder",
        final_state=final_state,
    )

    return exc, resolution
