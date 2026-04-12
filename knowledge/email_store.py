"""knowledge/email_store.py

Redis vector-search store for supplier/buyer email communications.

Why vector search?
------------------
Emails are unstructured. A query for "invoice from SafeGuard about price
increase" will not find the right email with a keyword match — the subject
might say "Re: Updated Pricing" and the body might never mention the word
"increase".  We embed the full searchable text of each email and use Redis's
HNSW index for approximate nearest-neighbour (ANN) search, which handles
paraphrases, abbreviations, and mixed-field queries naturally.

Storage layout
--------------
``kb:email:<email_id>``
    Redis Hash with fields:
        email_id, subject, sender, receiver, date (ISO), body,
        related_po, related_invoice,
        embedding  (BLOB — float32 LE, 384 dims for all-MiniLM-L6-v2)

``kb:email:idx``  (Redis Search index, HNSW, COSINE distance)
    Indexes all ``kb:email:*`` hashes.

The embedding is built from a composite text that concatenates:
    subject + sender + receiver + date + related_po + related_invoice + body

This means queries on any of those fields — even partial phrases — will
surface relevant emails via semantic similarity.

Querying
--------
    results = email_store.search(
        query="price dispute for PO-0023",
        top_k=5,
    )

Each result dict contains all hash fields plus a ``score`` (0.0–1.0,
higher = more similar).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import redis
from redis.commands.search.field import TagField, TextField, VectorField  # type: ignore
from redis.commands.search.index_definition import IndexDefinition, IndexType  # type: ignore
from redis.commands.search.query import Query  # type: ignore

from knowledge.embedder import Embedder
from models.communication import Email

logger = logging.getLogger(__name__)

_HASH_PREFIX = "kb:email:"
_INDEX_NAME = "kb:email:idx"


class EmailVectorStore:
    """Store and semantically search email communications using Redis vector search.

    Args:
        r: Active Redis connection (must point at a Redis Stack instance).
        embedder: Embedder instance for converting text to vectors.
        dimensions: Embedding vector dimension (must match embedder output).
    """

    def __init__(
        self,
        r: redis.Redis,
        embedder: Embedder,
        dimensions: int = 384,
    ) -> None:
        self._r = r
        self._embedder = embedder
        self._dimensions = dimensions

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def ensure_index(self) -> None:
        """Create the Redis Search index if it does not already exist.

        Safe to call on every startup (idempotent — no-op if already present).
        """
        try:
            self._r.ft(_INDEX_NAME).info()
            logger.debug("Email vector index '%s' already exists.", _INDEX_NAME)
            return
        except Exception:
            pass  # Index does not exist — create it below.

        schema = [
            TextField("email_id", no_stem=True),
            TextField("subject"),
            TextField("sender"),
            TextField("receiver"),
            TagField("date"),           # exact date filter support
            TagField("related_po"),     # exact PO filter support
            TagField("related_invoice"),
            VectorField(
                "embedding",
                "HNSW",
                {
                    "TYPE": "FLOAT32",
                    "DIM": self._dimensions,
                    "DISTANCE_METRIC": "COSINE",
                    "M": 16,
                    "EF_CONSTRUCTION": 200,
                },
            ),
        ]
        definition = IndexDefinition(
            prefix=[_HASH_PREFIX],
            index_type=IndexType.HASH,
        )
        self._r.ft(_INDEX_NAME).create_index(schema, definition=definition)
        logger.info("Created email vector index '%s' (dim=%d).", _INDEX_NAME, self._dimensions)

    def drop_index(self) -> None:
        """Drop the index (does NOT delete the underlying hashes)."""
        try:
            self._r.ft(_INDEX_NAME).dropindex()
            logger.info("Dropped email vector index '%s'.", _INDEX_NAME)
        except Exception as exc:
            logger.debug("drop_index: %s", exc)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, email: Email) -> None:
        """Store a single email, overwriting any existing entry for the same ID.

        The embedding is recomputed on every upsert so that changes to the
        composite search text are reflected immediately.
        """
        key = f"{_HASH_PREFIX}{email.email_id}"
        text = _build_search_text(email)
        embedding_blob = self._embedder.embed(text)

        mapping: dict[str, Any] = {
            "email_id": email.email_id,
            "subject": email.subject,
            "sender": email.sender,
            "receiver": email.receiver,
            "date": email.date.isoformat() if isinstance(email.date, date) else str(email.date),
            "body": email.body,
            "related_po": email.related_po or "",
            "related_invoice": email.related_invoice or "",
            "embedding": embedding_blob,
        }
        self._r.hset(key, mapping=mapping)
        logger.debug("EmailVectorStore: upserted %s", email.email_id)

    def upsert_batch(self, emails: list[Email]) -> None:
        """Upsert a batch of emails efficiently using a pipeline."""
        if not emails:
            return

        texts = [_build_search_text(e) for e in emails]
        blobs = self._embedder.embed_batch(texts)

        pipe = self._r.pipeline(transaction=False)
        for email, blob in zip(emails, blobs):
            key = f"{_HASH_PREFIX}{email.email_id}"
            mapping: dict[str, Any] = {
                "email_id": email.email_id,
                "subject": email.subject,
                "sender": email.sender,
                "receiver": email.receiver,
                "date": email.date.isoformat() if isinstance(email.date, date) else str(email.date),
                "body": email.body,
                "related_po": email.related_po or "",
                "related_invoice": email.related_invoice or "",
                "embedding": blob,
            }
            pipe.hset(key, mapping=mapping)
        pipe.execute()
        logger.info("EmailVectorStore: batch-upserted %d emails.", len(emails))

    # ------------------------------------------------------------------
    # Read / Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        date_filter: str | None = None,
        po_filter: str | None = None,
        invoice_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search over emails.

        Args:
            query: Free-text query — may reference a buyer/seller name, PO/invoice
                   number, exception type, or any topic mentioned in emails.
            top_k: Maximum number of results to return.
            date_filter: ISO date string (``'2026-01-15'``) to restrict by exact date.
            po_filter: Exact PO number to filter on (``'PO-0023'``).
            invoice_filter: Exact invoice number to filter on (``'INV-0023'``).

        Returns:
            List of dicts containing all hash fields plus ``score`` (0.0–1.0,
            higher = more similar to query).
        """
        blob = self._embedder.embed(query)

        # Build optional pre-filter expression
        prefilter = _build_prefilter(date_filter, po_filter, invoice_filter)
        base = f"({prefilter})=>" if prefilter else ""
        knn_clause = f"[KNN {top_k} @embedding $vec AS score]"
        query_str = f"{base}{knn_clause}"

        q = (
            Query(query_str)
            .sort_by("score")
            .return_fields(
                "email_id", "subject", "sender", "receiver",
                "date", "related_po", "related_invoice", "body", "score",
            )
            .dialect(2)
        )

        try:
            results = self._r.ft(_INDEX_NAME).search(q, query_params={"vec": blob})
        except Exception as exc:
            logger.error("EmailVectorStore.search failed: %s", exc)
            return []

        return _parse_results(results)

    def get(self, email_id: str) -> dict | None:
        """Fetch a single email by ID (no embedding returned)."""
        raw = self._r.hgetall(f"{_HASH_PREFIX}{email_id}")
        if not raw:
            return None
        record = dict(raw)
        record.pop("embedding", None)  # Don't return binary blob to callers
        return record

    def count(self) -> int:
        """Return the number of emails stored in this index."""
        try:
            info = self._r.ft(_INDEX_NAME).info()
            return int(info.get("num_docs", 0))
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_search_text(email: Email) -> str:
    """Produce the composite text that will be embedded for an email.

    Concatenating all searchable fields means a single vector captures
    sender, receiver, subject, date, PO/invoice references, and body.
    """
    parts = [
        email.subject,
        f"From: {email.sender}",
        f"To: {email.receiver}",
        f"Date: {email.date}",
    ]
    if email.related_po:
        parts.append(f"PO: {email.related_po}")
    if email.related_invoice:
        parts.append(f"Invoice: {email.related_invoice}")
    parts.append(email.body)
    return " | ".join(parts)


def _build_prefilter(
    date_filter: str | None,
    po_filter: str | None,
    invoice_filter: str | None,
) -> str:
    """Build a Redis Search filter expression from optional exact-match constraints."""
    clauses: list[str] = []
    if date_filter:
        escaped = date_filter.replace("-", r"\-")
        clauses.append(f"@date:{{{escaped}}}")
    if po_filter:
        escaped = po_filter.replace("-", r"\-")
        clauses.append(f"@related_po:{{{escaped}}}")
    if invoice_filter:
        escaped = invoice_filter.replace("-", r"\-")
        clauses.append(f"@related_invoice:{{{escaped}}}")
    return " ".join(clauses)


def _parse_results(results) -> list[dict]:
    """Convert a Redis Search result set into plain dicts."""
    output = []
    for doc in results.docs:
        record: dict[str, Any] = {}
        for field in (
            "email_id", "subject", "sender", "receiver",
            "date", "related_po", "related_invoice", "body", "score",
        ):
            val = getattr(doc, field, None)
            if val is not None:
                record[field] = val
        # Convert cosine distance score to similarity (1 - distance)
        if "score" in record:
            try:
                record["score"] = round(1.0 - float(record["score"]), 4)
            except (ValueError, TypeError):
                record["score"] = 0.0
        output.append(record)
    return output
