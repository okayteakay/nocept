from __future__ import annotations

import logging

import redis

from models.exception import ExceptionState, InvoiceException
from models.exception_record import ExceptionType
from models.resolution import Resolution
from state.machine import ExceptionStateMachine, InvalidTransitionError  # noqa: F401

logger = logging.getLogger(__name__)

_STATE_INDEX_PREFIX = "state_index:"


class RedisStateStore:
    """Persists and manages InvoiceException state in Redis.

    Storage layout::

        exception:<exception_id>              → JSON-serialized InvoiceException
        resolution:<exception_id>             → JSON-serialized Resolution
        supplier:<supplier_id>:exceptions     → Redis Set of exception_ids
        state_index:<state_value>             → Redis Set of exception_ids

    The state index is maintained on every ``save()`` call so that
    ``list_by_state()`` is a simple SMEMBERS rather than a full keyspace scan.
    """

    KEY_PREFIX = "exception:"
    RESOLUTION_PREFIX = "resolution:"
    SUPPLIER_INDEX_PREFIX = "supplier:"

    def __init__(self, r: redis.Redis) -> None:
        self._r = r

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def save(self, exc: InvoiceException) -> None:
        """Serialize and persist an InvoiceException to Redis.

        Maintains supplier and state indexes.  If the exception already exists
        in Redis and its state has changed, the old state index entry is removed.

        Args:
            exc: The InvoiceException to persist.
        """
        key = f"{self.KEY_PREFIX}{exc.exception_id}"

        # Remove from old state index if state changed
        existing_raw = self._r.get(key)
        if existing_raw:
            try:
                existing = InvoiceException.model_validate_json(existing_raw)
                if existing.state != exc.state:
                    old_state_key = f"{_STATE_INDEX_PREFIX}{existing.state.value}"
                    self._r.srem(old_state_key, exc.exception_id)
            except Exception as parse_err:
                logger.warning(
                    "Could not parse existing exception %s to update state index: %s",
                    exc.exception_id,
                    parse_err,
                )

        self._r.set(key, exc.model_dump_json())

        # Supplier index (Set — idempotent adds)
        supplier_key = (
            f"{self.SUPPLIER_INDEX_PREFIX}{exc.invoice.supplier_id}:exceptions"
        )
        self._r.sadd(supplier_key, exc.exception_id)

        # State index
        state_key = f"{_STATE_INDEX_PREFIX}{exc.state.value}"
        self._r.sadd(state_key, exc.exception_id)

        logger.debug(
            "Saved exception %s (state=%s, supplier=%s)",
            exc.exception_id,
            exc.state.value,
            exc.invoice.supplier_id,
        )

    def load(self, exception_id: str) -> InvoiceException:
        """Deserialize and return an InvoiceException from Redis.

        Args:
            exception_id: UUID of the exception.

        Returns:
            The deserialized InvoiceException.

        Raises:
            KeyError: If no exception exists for the given ID.
        """
        key = f"{self.KEY_PREFIX}{exception_id}"
        raw = self._r.get(key)
        if raw is None:
            raise KeyError(f"No exception found for ID: {exception_id!r}")
        return InvoiceException.model_validate_json(raw)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(self, exception_id: str, to: ExceptionState) -> InvoiceException:
        """Load an exception, validate the transition, update state, and re-save.

        Args:
            exception_id: UUID of the exception.
            to: Target state.

        Returns:
            The updated InvoiceException.

        Raises:
            KeyError: If the exception doesn't exist.
            InvalidTransitionError: If the transition is not permitted.
        """
        exc = self.load(exception_id)
        sm = ExceptionStateMachine(exc.state)
        sm.transition(to)  # raises InvalidTransitionError on invalid move

        exc.state = to
        exc.mark_updated()
        self.save(exc)

        logger.info(
            "Exception %s transitioned to %s", exception_id, to.value
        )
        return exc

    # ------------------------------------------------------------------
    # Resolutions
    # ------------------------------------------------------------------

    def save_resolution(self, resolution: Resolution) -> None:
        """Persist a Resolution record for a resolved exception.

        Args:
            resolution: The Resolution to persist.
        """
        key = f"{self.RESOLUTION_PREFIX}{resolution.exception_id}"
        self._r.set(key, resolution.model_dump_json())
        logger.debug("Saved resolution for exception %s", resolution.exception_id)

    def get_resolution(self, exception_id: str) -> Resolution | None:
        """Return the Resolution for an exception, or None if not yet resolved.

        Args:
            exception_id: UUID of the exception.

        Returns:
            The Resolution or None.
        """
        key = f"{self.RESOLUTION_PREFIX}{exception_id}"
        raw = self._r.get(key)
        if raw is None:
            return None
        return Resolution.model_validate_json(raw)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_by_state(self, state: ExceptionState) -> list[str]:
        """Return exception IDs currently in the given state.

        Uses the maintained state index (SMEMBERS) — O(n) where n is the
        number of exceptions in that state.

        Args:
            state: The state to filter on.

        Returns:
            List of exception_id strings.
        """
        state_key = f"{_STATE_INDEX_PREFIX}{state.value}"
        members = self._r.smembers(state_key)
        return [
            m.decode() if isinstance(m, bytes) else m
            for m in members
        ]

    def list_by_supplier(self, supplier_id: str) -> list[InvoiceException]:
        """Return all known exceptions for a supplier, ordered by creation time.

        Args:
            supplier_id: Supplier identifier.

        Returns:
            List of InvoiceException objects, oldest first.
        """
        supplier_key = f"{self.SUPPLIER_INDEX_PREFIX}{supplier_id}:exceptions"
        exception_ids = self._r.smembers(supplier_key)

        exceptions: list[InvoiceException] = []
        for eid in exception_ids:
            eid_str = eid.decode() if isinstance(eid, bytes) else eid
            try:
                exceptions.append(self.load(eid_str))
            except KeyError:
                logger.warning(
                    "Supplier index for %s references missing exception %s — skipping",
                    supplier_id,
                    eid_str,
                )

        return sorted(exceptions, key=lambda e: e.created_at)

    def get_supplier_pattern_summary(self, supplier_id: str) -> dict:
        """Return a summary of historical resolution patterns for a supplier.

        Used by the context retriever as a quick-read cache. Iterates all
        stored exceptions for the supplier to compute aggregate statistics.

        Returns a dict with at minimum:
        - ``total_exceptions``: int
        - ``resolved_count``: int
        - ``informal_modification_count``: int
        - ``avg_price_uplift_pct``: float | None

        Args:
            supplier_id: Supplier identifier.

        Returns:
            Dict with summary statistics.
        """
        exceptions = self.list_by_supplier(supplier_id)
        resolved_count = sum(
            1 for e in exceptions if e.state == ExceptionState.RESOLVED
        )
        informal_count = sum(
            1
            for e in exceptions
            if ExceptionType.INFORMAL_MODIFICATION in e.exception_types
        )

        # Collect price uplift percentages from line variances of informal mod exceptions
        uplifts: list[float] = []
        for e in exceptions:
            if ExceptionType.INFORMAL_MODIFICATION not in e.exception_types:
                continue
            for v in e.line_variances:
                if v.price_delta_pct is not None and v.price_delta_pct > 0:
                    uplifts.append(v.price_delta_pct)

        avg_price_uplift_pct = (sum(uplifts) / len(uplifts)) if uplifts else None

        return {
            "total_exceptions": len(exceptions),
            "resolved_count": resolved_count,
            "informal_modification_count": informal_count,
            "avg_price_uplift_pct": avg_price_uplift_pct,
        }
