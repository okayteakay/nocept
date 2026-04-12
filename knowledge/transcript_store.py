"""knowledge/transcript_store.py

Redis vector-search store for phone call transcripts.

Why vector search?
------------------
Transcripts are fully unstructured conversation text. There is no subject
line. The only structure is who participated (caller/callee + organizations),
the date, and the related PO/invoice references (which may be absent). Vector
search over the full transcript text handles questions like:
  - "Find all calls where a price increase was discussed"
  - "Show me calls between Meridian Corp and SafeGuard PPE"
  - "Any transcripts related to PO-0045 or product substitutions?"

Storage layout
--------------
``kb:transcript:<transcript_id>``
    Redis Hash with fields:
        transcript_id, caller, caller_organization,
        callee, callee_organization, date (ISO),
        duration_minutes, transcript (full text),
        related_po, related_invoice,
        embedding  (BLOB — float32 LE)

``kb:transcript:idx``  (Redis Search HNSW index, COSINE)
    Indexes all ``kb:transcript:*`` hashes.

The embedding is built from the full transcript text prefixed with caller,
callee, organizations, date, and any PO/invoice references — so structured
queries like "calls with supplier X" are handled by the same vector index.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import redis
from redis.commands.search.field import TagField, TextField, NumericField, VectorField  # type: ignore
from redis.commands.search.index_definition import IndexDefinition, IndexType  # type: ignore
from redis.commands.search.query import Query  # type: ignore

from knowledge.embedder import Embedder
from models.communication import PhoneTranscript

logger = logging.getLogger(__name__)

_HASH_PREFIX = "kb:transcript:"
_INDEX_NAME = "kb:transcript:idx"


class TranscriptVectorStore:
    """Store and semantically search phone call transcripts using Redis vector search.

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
        """Create the Redis Search index if it does not already exist (idempotent)."""
        try:
            self._r.ft(_INDEX_NAME).info()
            logger.debug("Transcript vector index '%s' already exists.", _INDEX_NAME)
            return
        except Exception:
            pass

        schema = [
            TextField("transcript_id", no_stem=True),
            TextField("caller"),
            TextField("caller_organization"),
            TextField("callee"),
            TextField("callee_organization"),
            TagField("date"),
            NumericField("duration_minutes"),
            TagField("related_po"),
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
        logger.info(
            "Created transcript vector index '%s' (dim=%d).", _INDEX_NAME, self._dimensions
        )

    def drop_index(self) -> None:
        """Drop the index (does NOT delete the underlying hashes)."""
        try:
            self._r.ft(_INDEX_NAME).dropindex()
            logger.info("Dropped transcript vector index '%s'.", _INDEX_NAME)
        except Exception as exc:
            logger.debug("drop_index: %s", exc)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, transcript: PhoneTranscript) -> None:
        """Store a single transcript, overwriting any existing entry for the same ID."""
        key = f"{_HASH_PREFIX}{transcript.transcript_id}"
        text = _build_search_text(transcript)
        embedding_blob = self._embedder.embed(text)

        mapping: dict[str, Any] = {
            "transcript_id": transcript.transcript_id,
            "caller": transcript.caller,
            "caller_organization": transcript.caller_organization,
            "callee": transcript.callee,
            "callee_organization": transcript.callee_organization,
            "date": (
                transcript.date.isoformat()
                if isinstance(transcript.date, date)
                else str(transcript.date)
            ),
            "duration_minutes": str(transcript.duration_minutes),
            "transcript": transcript.transcript,
            "related_po": transcript.related_po or "",
            "related_invoice": transcript.related_invoice or "",
            "embedding": embedding_blob,
        }
        self._r.hset(key, mapping=mapping)
        logger.debug("TranscriptVectorStore: upserted %s", transcript.transcript_id)

    def upsert_batch(self, transcripts: list[PhoneTranscript]) -> None:
        """Upsert a batch of transcripts efficiently using a pipeline."""
        if not transcripts:
            return

        texts = [_build_search_text(t) for t in transcripts]
        blobs = self._embedder.embed_batch(texts)

        pipe = self._r.pipeline(transaction=False)
        for transcript, blob in zip(transcripts, blobs):
            key = f"{_HASH_PREFIX}{transcript.transcript_id}"
            mapping: dict[str, Any] = {
                "transcript_id": transcript.transcript_id,
                "caller": transcript.caller,
                "caller_organization": transcript.caller_organization,
                "callee": transcript.callee,
                "callee_organization": transcript.callee_organization,
                "date": (
                    transcript.date.isoformat()
                    if isinstance(transcript.date, date)
                    else str(transcript.date)
                ),
                "duration_minutes": str(transcript.duration_minutes),
                "transcript": transcript.transcript,
                "related_po": transcript.related_po or "",
                "related_invoice": transcript.related_invoice or "",
                "embedding": blob,
            }
            pipe.hset(key, mapping=mapping)
        pipe.execute()
        logger.info(
            "TranscriptVectorStore: batch-upserted %d transcripts.", len(transcripts)
        )

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
        """Semantic search over transcripts.

        Args:
            query: Free-text query. May reference names, organizations, PO/invoice
                   numbers, product names, or any topic discussed in calls.
            top_k: Maximum results to return.
            date_filter: Exact ISO date (``'2026-01-15'``) to restrict results.
            po_filter: Exact PO number (``'PO-0045'``) to restrict results.
            invoice_filter: Exact invoice number to restrict results.

        Returns:
            List of dicts with all hash fields (except embedding BLOB) plus
            ``score`` (0.0–1.0, higher = more similar).
        """
        blob = self._embedder.embed(query)

        prefilter = _build_prefilter(date_filter, po_filter, invoice_filter)
        base = f"({prefilter})=>" if prefilter else ""
        knn_clause = f"[KNN {top_k} @embedding $vec AS score]"
        query_str = f"{base}{knn_clause}"

        q = (
            Query(query_str)
            .sort_by("score")
            .return_fields(
                "transcript_id", "caller", "caller_organization",
                "callee", "callee_organization", "date",
                "duration_minutes", "related_po", "related_invoice",
                "transcript", "score",
            )
            .dialect(2)
        )

        try:
            results = self._r.ft(_INDEX_NAME).search(q, query_params={"vec": blob})
        except Exception as exc:
            logger.error("TranscriptVectorStore.search failed: %s", exc)
            return []

        return _parse_results(results)

    def get(self, transcript_id: str) -> dict | None:
        """Fetch a single transcript by ID (embedding BLOB excluded)."""
        raw = self._r.hgetall(f"{_HASH_PREFIX}{transcript_id}")
        if not raw:
            return None
        record = dict(raw)
        record.pop("embedding", None)
        return record

    def count(self) -> int:
        """Return the number of transcripts stored in this index."""
        try:
            info = self._r.ft(_INDEX_NAME).info()
            return int(info.get("num_docs", 0))
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_search_text(t: PhoneTranscript) -> str:
    """Build composite search text from all meaningful fields of a transcript.

    The structured metadata (caller names, organizations, date, PO/invoice)
    is prepended so that queries on those fields are weighted appropriately
    by the embedding model.
    """
    parts = [
        f"Caller: {t.caller} ({t.caller_organization})",
        f"Callee: {t.callee} ({t.callee_organization})",
        f"Date: {t.date}",
        f"Duration: {t.duration_minutes} minutes",
    ]
    if t.related_po:
        parts.append(f"PO: {t.related_po}")
    if t.related_invoice:
        parts.append(f"Invoice: {t.related_invoice}")
    parts.append(t.transcript)
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
            "transcript_id", "caller", "caller_organization",
            "callee", "callee_organization", "date",
            "duration_minutes", "related_po", "related_invoice",
            "transcript", "score",
        ):
            val = getattr(doc, field, None)
            if val is not None:
                record[field] = val
        if "score" in record:
            try:
                record["score"] = round(1.0 - float(record["score"]), 4)
            except (ValueError, TypeError):
                record["score"] = 0.0
        output.append(record)
    return output
