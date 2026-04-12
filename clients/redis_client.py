from __future__ import annotations

import logging
from typing import Any

import redis
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)


def get_redis_connection(url: str) -> redis.Redis:
    """Create and return a Redis connection from a connection URL.

    The connection is lazy — it won't actually connect until the first command
    is issued. Call r.ping() to verify connectivity.

    Args:
        url: Redis connection string, e.g. "redis://localhost:6379/0"

    Returns:
        A configured redis.Redis client instance.
    """
    return redis.Redis.from_url(url, decode_responses=True)


class RedisStreamsClient:
    """Thin wrapper around Redis Streams commands for append-only event logging.

    All entries are stored as flat string key-value dicts (Redis requirement).
    Callers are responsible for serializing complex values to strings before
    passing them in, and deserializing on read.
    """

    def __init__(self, r: redis.Redis, stream_name: str) -> None:
        """
        Args:
            r: An active redis.Redis connection.
            stream_name: The Redis key to use for the stream (e.g. "ap:audit:events").
        """
        self._r = r
        self._stream = stream_name

    def append(self, fields: dict[str, str]) -> str:
        """Append an entry to the stream (XADD * ...).

        Args:
            fields: Flat dict of string keys and string values.

        Returns:
            The Redis stream entry ID (e.g. "1712345678901-0").
        """
        return self._r.xadd(self._stream, fields)

    def read_range(self, start: str = "-", end: str = "+") -> list[dict[str, Any]]:
        """Read all entries in the stream between start and end IDs (XRANGE).

        Args:
            start: Lower bound entry ID, or "-" for the beginning.
            end: Upper bound entry ID, or "+" for the end.

        Returns:
            List of dicts with keys "id" and "fields".
        """
        entries = self._r.xrange(self._stream, min=start, max=end)
        return [
            {"id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id, "fields": fields}
            for entry_id, fields in entries
        ]

    def read_group(
        self,
        group: str,
        consumer: str,
        count: int = 10,
    ) -> list[dict[str, Any]]:
        """Read pending entries for a consumer group (XREADGROUP).

        Args:
            group: Consumer group name.
            consumer: Consumer identity within the group.
            count: Maximum number of entries to return.

        Returns:
            List of dicts with keys "id" and "fields".
        """
        # XREADGROUP GROUP group consumer COUNT count STREAMS stream >
        results = self._r.xreadgroup(
            group=group,
            consumer=consumer,
            streams={self._stream: ">"},
            count=count,
        )

        output = []
        for stream, messages in results:
            for message in messages:
                entry_id, fields = message
                output.append({
                    "id": entry_id.decode() if isinstance(entry_id, bytes) else entry_id,
                    "fields": fields
                })
        return output

    def create_group(self, group: str, mkstream: bool = True) -> None:
        """Create a consumer group on this stream (XGROUP CREATE).

        Args:
            group: Group name.
            mkstream: If True, create the stream if it doesn't exist.
        """
        try:
            self._r.xgroup_create(
                name=self._stream,
                groupname=group,
                id="0",
                mkstream=mkstream
            )
        except redis.exceptions.ResponseError as e:
            if "already exists" in str(e):
                logger.debug("Consumer group %s already exists for stream %s", group, self._stream)
            else:
                raise

    def ack(self, group: str, entry_id: str) -> None:
        """Acknowledge a processed entry in a consumer group (XACK).

        Args:
            group: Consumer group name.
            entry_id: The stream entry ID returned by read_group.
        """
        self._r.xack(self._stream, group, entry_id)
