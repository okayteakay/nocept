"""knowledge/resolution_store.py

Redis-backed store for resolved and escalated exception history.

Every finalized exception (state == RESOLVED or ESCALATED) is indexed here so
that the agent and human reviewers can quickly answer questions like:
  - "Show me all resolved cases for supplier SUP-008"
  - "What happened to PO-0023?"
  - "Give me every case involving SKU GLOVE-NITRILE-STD"

Storage layout
--------------
``kb:res:<exception_id>``
    Redis Hash — full resolution record (flat string fields).
    Fields: exception_id, po_number, invoice_number, supplier_id,
            supplier_name, exception_types, final_state, action,
            root_cause, confidence, summary, variance_amount,
            variance_skus, resolved_at, resolved_by

``kb:res:idx:supplier:<supplier_id>``
    Redis Sorted Set — exception_ids scored by resolved_at (unix timestamp).
    Supports chronological range queries per supplier.

``kb:res:idx:po:<po_number>``
    Redis Sorted Set — exception_ids scored by resolved_at.

``kb:res:idx:invoice:<invoice_number>``
    Redis Sorted Set — exception_ids scored by resolved_at.

``kb:res:idx:sku:<sku>``
    Redis Sorted Set — exception_ids for cases that touched this SKU.

``kb:res:idx:state:<state>``
    Redis Sorted Set — exception_ids for RESOLVED / ESCALATED respectively.

All keys live under the ``kb:`` namespace (configurable via AppConfig).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Sequence

import redis

from models.exception import InvoiceException
from models.exception_record import ExceptionType
from models.resolution import Resolution

logger = logging.getLogger(__name__)

_HASH_PREFIX = "kb:res:"
_IDX_SUPPLIER = "kb:res:idx:supplier:"
_IDX_PO = "kb:res:idx:po:"
_IDX_INVOICE = "kb:res:idx:invoice:"
_IDX_SKU = "kb:res:idx:sku:"
_IDX_STATE = "kb:res:idx:state:"


class ResolutionHistoryStore:
    """Persist and query finalized exception resolution records in Redis.

    This store is append-oriented: records are written once when a case is
    finalized and never modified.  All queries return the most-recent records
    first (ZREVRANGE semantics).
    """

    def __init__(self, r: redis.Redis) -> None:
        self._r = r

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, exc: InvoiceException, resolution: Resolution) -> None:
        """Persist a finalized resolution record and update all indexes.

        Safe to call multiple times for the same exception_id — later calls
        overwrite the hash but re-use the same index entries (idempotent).

        Args:
            exc: The fully-classified InvoiceException (carries invoice, PO,
                 GRN, line variances, exception types).
            resolution: The Resolution produced by the pipeline or a human.
        """
        eid = exc.exception_id
        score = _unix_ts(resolution.resolved_at)

        # --- Build the flat hash record ----------------------------------
        exception_types_str = ",".join(t.value for t in exc.exception_types) or "none"
        variance_skus = _collect_skus(exc)

        record: dict[str, str] = {
            "exception_id": eid,
            "po_number": exc.purchase_order.po_number,
            "invoice_number": exc.invoice.invoice_number,
            "supplier_id": exc.invoice.supplier_id,
            "supplier_name": exc.invoice.supplier_name,
            "exception_types": exception_types_str,
            "final_state": resolution.final_state.value,
            "action": resolution.memo.action.value,
            "root_cause": resolution.memo.root_cause.value,
            "confidence": f"{resolution.memo.confidence:.4f}",
            "summary": resolution.memo.summary,
            "variance_amount": f"{exc.total_variance_usd:.2f}",
            "variance_skus": json.dumps(variance_skus),
            "resolved_at": resolution.resolved_at.isoformat(),
            "resolved_by": resolution.resolved_by,
        }

        pipe = self._r.pipeline(transaction=True)
        pipe.hset(f"{_HASH_PREFIX}{eid}", mapping=record)
        pipe.zadd(f"{_IDX_SUPPLIER}{exc.invoice.supplier_id}", {eid: score})
        pipe.zadd(f"{_IDX_PO}{exc.purchase_order.po_number}", {eid: score})
        pipe.zadd(f"{_IDX_INVOICE}{exc.invoice.invoice_number}", {eid: score})
        pipe.zadd(f"{_IDX_STATE}{resolution.final_state.value}", {eid: score})
        for sku in variance_skus:
            pipe.zadd(f"{_IDX_SKU}{sku}", {eid: score})
        pipe.execute()

        logger.debug(
            "ResolutionHistoryStore: upserted %s (supplier=%s, state=%s)",
            eid,
            exc.invoice.supplier_id,
            resolution.final_state.value,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, exception_id: str) -> dict | None:
        """Return the resolution record for a single exception, or None."""
        raw = self._r.hgetall(f"{_HASH_PREFIX}{exception_id}")
        return dict(raw) if raw else None

    def by_supplier(
        self,
        supplier_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Return up to *limit* resolved cases for a supplier, newest first."""
        return self._fetch_by_index(f"{_IDX_SUPPLIER}{supplier_id}", limit)

    def by_po(self, po_number: str, limit: int = 20) -> list[dict]:
        """Return resolved cases for a PO number, newest first."""
        return self._fetch_by_index(f"{_IDX_PO}{po_number}", limit)

    def by_invoice(self, invoice_number: str, limit: int = 20) -> list[dict]:
        """Return resolved cases for an invoice number, newest first."""
        return self._fetch_by_index(f"{_IDX_INVOICE}{invoice_number}", limit)

    def by_sku(self, sku: str, limit: int = 50) -> list[dict]:
        """Return resolved cases that involved a specific SKU, newest first."""
        return self._fetch_by_index(f"{_IDX_SKU}{sku}", limit)

    def by_state(
        self,
        state: str,
        limit: int = 100,
    ) -> list[dict]:
        """Return cases by final state (``'resolved'`` or ``'escalated'``), newest first."""
        return self._fetch_by_index(f"{_IDX_STATE}{state}", limit)

    def supplier_summary(self, supplier_id: str) -> dict:
        """Return aggregate statistics for a supplier's resolution history.

        Returns a dict with:
        - ``total``: total finalized cases
        - ``resolved``: auto-resolved count
        - ``escalated``: human-escalated count
        - ``exception_type_counts``: {type: count}
        - ``avg_confidence``: mean agent confidence across auto-resolved cases
        - ``recent``: list of the 5 most recent summaries
        """
        records = self.by_supplier(supplier_id, limit=200)
        if not records:
            return {
                "total": 0,
                "resolved": 0,
                "escalated": 0,
                "exception_type_counts": {},
                "avg_confidence": None,
                "recent": [],
            }

        resolved = sum(1 for r in records if r.get("final_state") == "resolved")
        escalated = sum(1 for r in records if r.get("final_state") == "escalated")

        type_counts: dict[str, int] = {}
        confidences: list[float] = []
        for r in records:
            for t in r.get("exception_types", "none").split(","):
                type_counts[t] = type_counts.get(t, 0) + 1
            try:
                confidences.append(float(r["confidence"]))
            except (KeyError, ValueError):
                pass

        avg_confidence = sum(confidences) / len(confidences) if confidences else None

        return {
            "total": len(records),
            "resolved": resolved,
            "escalated": escalated,
            "exception_type_counts": type_counts,
            "avg_confidence": avg_confidence,
            "recent": records[:5],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_by_index(self, index_key: str, limit: int) -> list[dict]:
        """Read exception_ids from a sorted-set index (newest first), fetch hashes."""
        eids = self._r.zrevrange(index_key, 0, limit - 1)
        results = []
        for eid in eids:
            eid_str = eid if isinstance(eid, str) else eid.decode()
            record = self._r.hgetall(f"{_HASH_PREFIX}{eid_str}")
            if record:
                results.append(dict(record))
        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _unix_ts(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _collect_skus(exc: InvoiceException) -> list[str]:
    """Return unique SKUs from an exception's line variances."""
    seen: set[str] = set()
    skus: list[str] = []
    for v in exc.line_variances:
        if v.sku and v.sku not in seen:
            seen.add(v.sku)
            skus.append(v.sku)
    # Also include invoice line items in case there are no variances (clean invoices)
    for item in exc.invoice.line_items:
        if item.sku and item.sku not in seen:
            seen.add(item.sku)
            skus.append(item.sku)
    return skus
