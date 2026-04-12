"""knowledge/client.py

KnowledgeBaseClient — single entry point for all three knowledge-base stores.

Usage pattern
-------------
At application startup::

    from knowledge.client import KnowledgeBaseClient
    from knowledge.seeder import seed_knowledge_base
    from ingestion.json_ingestor import load_dataset

    kb = KnowledgeBaseClient.from_config(r, config)
    bundle = load_dataset()
    seed_knowledge_base(bundle, kb.resolutions, kb.emails, kb.transcripts)

After a case is finalized by the pipeline::

    result = run_pipeline(invoice, po, grn, store, tavily, audit, config)
    kb.ingest_resolved_case(result.exception, result.resolution)
    # Optionally attach communications evidence that arrived during the case:
    kb.ingest_email(new_email)
    kb.ingest_transcript(new_transcript)

Querying::

    # Semantic search across emails
    hits = kb.search_emails("price dispute for PO-0023", top_k=5)

    # Semantic search across transcripts
    hits = kb.search_transcripts("product substitution call SafeGuard", top_k=5)

    # Structured lookup of resolution history
    history = kb.resolutions.by_supplier("SUP-008", limit=20)
    summary = kb.resolutions.supplier_summary("SUP-008")
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis

from config.settings import AppConfig
from knowledge.embedder import Embedder
from knowledge.email_store import EmailVectorStore
from knowledge.resolution_store import ResolutionHistoryStore
from knowledge.transcript_store import TranscriptVectorStore
from models.communication import Email, PhoneTranscript
from models.exception import InvoiceException
from models.resolution import Resolution

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class KnowledgeBaseClient:
    """Unified facade for resolution history, email search, and transcript search.

    All three stores share a single Embedder instance so the model is loaded
    at most once per process.

    Attributes:
        resolutions: ResolutionHistoryStore — structured lookups by supplier,
                     PO, invoice, SKU, state.
        emails: EmailVectorStore — semantic search over email communications.
        transcripts: TranscriptVectorStore — semantic search over call transcripts.
    """

    def __init__(
        self,
        resolutions: ResolutionHistoryStore,
        emails: EmailVectorStore,
        transcripts: TranscriptVectorStore,
    ) -> None:
        self.resolutions = resolutions
        self.emails = emails
        self.transcripts = transcripts

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, r: redis.Redis, config: AppConfig) -> "KnowledgeBaseClient":
        """Construct a KnowledgeBaseClient wired to the given Redis connection.

        The Embedder is shared across all three stores to avoid loading the
        model multiple times.

        Args:
            r: Active Redis connection (must point at Redis Stack).
            config: Application configuration (provides embedding_model,
                    vector_dimensions).
        """
        embedder = Embedder(model_name=config.embedding_model)
        dims = config.vector_dimensions

        return cls(
            resolutions=ResolutionHistoryStore(r),
            emails=EmailVectorStore(r, embedder, dimensions=dims),
            transcripts=TranscriptVectorStore(r, embedder, dimensions=dims),
        )

    # ------------------------------------------------------------------
    # Ingest (live data — called after each resolved case)
    # ------------------------------------------------------------------

    def ingest_resolved_case(
        self,
        exc: InvoiceException,
        resolution: Resolution,
    ) -> None:
        """Persist a newly resolved/escalated case into the resolution history store.

        Should be called immediately after ``run_pipeline()`` returns.
        Also ensures vector indexes exist (safe no-op if already present).

        Args:
            exc: The fully-classified InvoiceException (carries invoice, PO,
                 GRN, exception types, line variances, state).
            resolution: The Resolution produced by the pipeline or a human.
        """
        self.emails.ensure_index()
        self.transcripts.ensure_index()
        self.resolutions.upsert(exc, resolution)
        logger.info(
            "KnowledgeBaseClient: ingested resolved case %s (state=%s)",
            exc.exception_id,
            resolution.final_state.value,
        )

    def ingest_email(self, email: Email) -> None:
        """Add or update a single email in the vector index.

        Call this when a new email arrives for an open or recently-closed case.

        Args:
            email: The Email object to persist.
        """
        self.emails.ensure_index()
        self.emails.upsert(email)
        logger.debug("KnowledgeBaseClient: ingested email %s", email.email_id)

    def ingest_transcript(self, transcript: PhoneTranscript) -> None:
        """Add or update a single phone transcript in the vector index.

        Args:
            transcript: The PhoneTranscript object to persist.
        """
        self.transcripts.ensure_index()
        self.transcripts.upsert(transcript)
        logger.debug(
            "KnowledgeBaseClient: ingested transcript %s", transcript.transcript_id
        )

    # ------------------------------------------------------------------
    # Search shortcuts (delegates to stores)
    # ------------------------------------------------------------------

    def search_emails(
        self,
        query: str,
        top_k: int = 10,
        date_filter: str | None = None,
        po_filter: str | None = None,
        invoice_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search across all stored emails.

        Args:
            query: Free-text query (buyer/seller name, PO number, topic, etc.).
            top_k: Maximum results.
            date_filter: Exact ISO date string to restrict results.
            po_filter: Exact PO number.
            invoice_filter: Exact invoice number.

        Returns:
            List of result dicts with email fields + ``score`` (0–1, higher = better match).
        """
        return self.emails.search(
            query,
            top_k=top_k,
            date_filter=date_filter,
            po_filter=po_filter,
            invoice_filter=invoice_filter,
        )

    def search_transcripts(
        self,
        query: str,
        top_k: int = 10,
        date_filter: str | None = None,
        po_filter: str | None = None,
        invoice_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search across all stored phone transcripts.

        Args:
            query: Free-text query (buyer/seller name, organization, topic, etc.).
            top_k: Maximum results.
            date_filter: Exact ISO date string to restrict results.
            po_filter: Exact PO number.
            invoice_filter: Exact invoice number.

        Returns:
            List of result dicts with transcript fields + ``score`` (0–1, higher = better).
        """
        return self.transcripts.search(
            query,
            top_k=top_k,
            date_filter=date_filter,
            po_filter=po_filter,
            invoice_filter=invoice_filter,
        )

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Return document counts for all three stores."""
        return {
            "emails": self.emails.count(),
            "transcripts": self.transcripts.count(),
        }
