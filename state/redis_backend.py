from __future__ import annotations

import json
import logging

import redis

from models.exception import ExceptionState, InvoiceException
from models.resolution import Resolution
from state.machine import ExceptionStateMachine

logger = logging.getLogger(__name__)


class RedisStateStore:
    """Persists and manages InvoiceException state in Redis.

    Each exception is stored as a JSON string under the key
    ``exception:<exception_id>``. Resolutions are stored under
    ``resolution:<exception_id>``. Supplier indexes are maintained
    under ``supplier:<supplier_id>:exceptions`` (a Redis Set).
    """

    KEY_PREFIX = "exception:"
    RESOLUTION_PREFIX = "resolution:"
    SUPPLIER_INDEX_PREFIX = "supplier:"

    def __init__(self, r: redis.Redis) -> None:
        self._r = r

    def save(self, exc: InvoiceException) -> None:
        """Serialize and persist an InvoiceException to Redis.

        Also updates the supplier index so exceptions can be listed by supplier.

        Args:
            exc: The exception to persist.
        """
        raise NotImplementedError

    def load(self, exception_id: str) -> InvoiceException:
        """Deserialize and return an InvoiceException from Redis.

        Args:
            exception_id: UUID of the exception.

        Returns:
            The InvoiceException model.

        Raises:
            KeyError: If no exception exists for the given ID.
        """
        raise NotImplementedError

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
        raise NotImplementedError

    def save_resolution(self, resolution: Resolution) -> None:
        """Persist a Resolution record for a resolved exception.

        Args:
            resolution: The resolution to persist.
        """
        raise NotImplementedError

    def get_resolution(self, exception_id: str) -> Resolution | None:
        """Return the Resolution for an exception, or None if not yet resolved.

        Args:
            exception_id: UUID of the exception.
        """
        raise NotImplementedError

    def list_by_state(self, state: ExceptionState) -> list[str]:
        """Return exception IDs currently in the given state.

        Scans the Redis keyspace — not suitable for high-frequency polling in
        production. Use a dedicated sorted set or index for that pattern.

        Args:
            state: The state to filter on.

        Returns:
            List of exception_id strings.
        """
        raise NotImplementedError

    def list_by_supplier(self, supplier_id: str) -> list[InvoiceException]:
        """Return all known exceptions for a supplier.

        Args:
            supplier_id: Supplier identifier.

        Returns:
            List of InvoiceException objects, oldest first.
        """
        raise NotImplementedError

    def get_supplier_pattern_summary(self, supplier_id: str) -> dict:
        """Return a summary of historical resolution patterns for a supplier.

        Intended as a quick-read cache for the context retriever. The exact
        schema is implementation-defined but should include at minimum:
        - total_exceptions: int
        - resolved_count: int
        - informal_modification_count: int
        - avg_price_uplift_pct: float | None

        Args:
            supplier_id: Supplier identifier.

        Returns:
            A dict with summary statistics.
        """
        raise NotImplementedError
