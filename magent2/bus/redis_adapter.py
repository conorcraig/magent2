from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from typing import Any

from magent2.observability import get_json_logger, get_metrics

from .interface import Bus, BusMessage


class RedisBus(Bus):
    """Redis Streams-backed Bus adapter.

    - publish: XADD to stream named by topic
    - read (no group): tail via XRANGE/XREVRANGE, supports last_id (uuid or entry id)
    - read (group set): XREADGROUP with safe group creation and XACK after read
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        group_name: str | None = None,
        consumer_name: str | None = None,
        block_ms: int | None = None,
        client: Any | None = None,
    ) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - import-time error path
            raise RuntimeError("redis package is required for RedisBus") from exc

        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        if client is not None:
            self._redis = client
        else:
            # decode_responses=True returns str everywhere for easier JSON handling
            self._redis = redis.from_url(url, decode_responses=True)

        self._group = group_name
        self._consumer = consumer_name or f"consumer-{uuid.uuid4()}"
        self._block_ms = block_ms

    # ----------------------------
    # Public API
    # ----------------------------
    def get_client(self) -> Any:
        """Return the underlying Redis client object.

        Exposed for advanced use cases such as idempotency sets or short-lived
        locks that are orthogonal to the Bus publish/read semantics.
        """
        return self._redis

    def publish(self, topic: str, message: BusMessage) -> str:  # noqa: D401
        """Append one message to a topic. Returns the Bus message id (uuid)."""
        # Store canonical id and payload JSON in the stream entry
        fields = {
            "id": message.id,
            "payload": json.dumps(message.payload, separators=(",", ":")),
        }
        # We ignore the returned entry id here and keep the canonical uuid as the Bus id
        self._redis.xadd(topic, fields)
        return message.id

    def read(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
    ) -> Iterable[BusMessage]:  # noqa: D401
        """Read messages after last_id (or tail if None)."""
        if self._group:
            return list(self._read_with_group(topic, limit))
        return list(self._read_without_group(topic, last_id, limit))

    def read_blocking(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
        block_ms: int = 1000,
    ) -> Iterable[BusMessage]:  # noqa: D401
        """Block up to block_ms waiting for messages after last_id."""
        if self._group:
            return list(self._read_blocking_with_group(topic, limit, block_ms))
        return list(self._read_blocking_without_group(topic, last_id, limit, block_ms))

    # ----------------------------
    # Internal helpers
    # ----------------------------
    def _read_without_group(
        self, topic: str, last_id: str | None, limit: int
    ) -> Iterable[BusMessage]:
        if last_id is None:
            return self._tail_messages(topic, limit)

        # Fast path: if last_id looks like a Redis entry id, seek after it
        if self._is_entry_id(last_id):
            return self._collect_after_cursor(topic, last_id, limit)

        # Otherwise, scan for the matching uuid in the 'id' field, then collect
        chunk_size = max(limit * 2, 100)
        cursor_id = self._scan_for_uuid(topic, last_id, chunk_size)
        if cursor_id is None:
            return []
        return self._collect_after_cursor(topic, cursor_id, limit)

    def _tail_messages(self, topic: str, limit: int) -> list[BusMessage]:
        entries = self._redis.xrevrange(topic, "+", "-", count=limit) or []
        entries.reverse()
        return [self._to_bus_message(topic, data, entry_id) for entry_id, data in entries]

    @staticmethod
    def _is_entry_id(value: str) -> bool:
        # Redis stream IDs are typically of the form '<milliseconds>-<sequence>'
        if "-" not in value:
            return False
        left, _, right = value.partition("-")
        return left.isdigit() and right.isdigit()

    def _scan_for_uuid(self, topic: str, last_uuid: str, chunk_size: int) -> str | None:
        cursor = "-"
        while True:
            start = cursor if cursor == "-" else f"({cursor}"
            chunk = self._redis.xrange(topic, start, "+", count=chunk_size) or []
            if not chunk:
                return None
            for entry_id, data in chunk:
                if data.get("id") == last_uuid:
                    return entry_id
            cursor = chunk[-1][0]

    def _collect_after_cursor(self, topic: str, cursor_id: str, limit: int) -> list[BusMessage]:
        messages: list[BusMessage] = []
        next_id = cursor_id
        while len(messages) < limit:
            # Read strictly after the cursor id
            start = f"({next_id}"
            remaining = limit - len(messages)
            chunk = self._redis.xrange(topic, start, "+", count=remaining) or []
            if not chunk:
                break
            for entry_id, data in chunk:
                messages.append(self._to_bus_message(topic, data, entry_id))
                next_id = entry_id
                if len(messages) >= limit:
                    break
        return messages

    def _read_with_group(self, topic: str, limit: int) -> Iterable[BusMessage]:
        self._ensure_group(topic)

        # Only read messages never delivered to the group ("new"), not pending ones
        resp = self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={topic: ">"},
            count=limit,
            block=(self._block_ms if self._block_ms is not None else 0),
        )

        if not resp:
            return []

        # resp shape: [(stream, [(entry_id, {field: value, ...}), ...])]
        _, items = resp[0]
        messages: list[BusMessage] = []
        for entry_id, data in items:
            messages.append(self._to_bus_message(topic, data, entry_id))
            try:
                # Acknowledge after conversion so we always deliver at-least-once semantics
                self._redis.xack(topic, self._group, entry_id)
            except Exception:
                # If ack fails, proceed; tests will still detect pending if it occurs
                logger = get_json_logger("magent2.bus")
                logger.warning(
                    "redis xack failed",
                    extra={
                        "event": "redis_xack_failed",
                        "topic": topic,
                        "group": str(self._group),
                        "entry_id": entry_id,
                    },
                )
                get_metrics().increment("bus_ack_failures", {"topic": topic})
        return messages

    def _read_blocking_without_group(
        self, topic: str, last_id: str | None, limit: int, block_ms: int
    ) -> Iterable[BusMessage]:
        # Determine the starting id for XREAD
        if last_id is None:
            start_id = "$"  # only new messages
        elif self._is_entry_id(last_id):
            start_id = last_id
        else:
            # Try to resolve uuid to an entry id; if not found, tail from current end
            resolved = self._scan_for_uuid(topic, last_id, max(limit * 2, 100))
            start_id = resolved if resolved is not None else "$"

        resp = self._redis.xread(streams={topic: start_id}, count=limit, block=block_ms) or []
        if not resp:
            return []
        # resp shape: [(stream, [(entry_id, {field: value, ...}), ...])]
        _, items = resp[0]
        return [self._to_bus_message(topic, data, entry_id) for entry_id, data in items]

    def _read_blocking_with_group(
        self, topic: str, limit: int, block_ms: int
    ) -> Iterable[BusMessage]:
        self._ensure_group(topic)

        resp = self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={topic: ">"},
            count=limit,
            block=block_ms,
        )

        if not resp:
            return []

        _, items = resp[0]
        messages: list[BusMessage] = []
        for entry_id, data in items:
            messages.append(self._to_bus_message(topic, data, entry_id))
            try:
                self._redis.xack(topic, self._group, entry_id)
            except Exception:
                pass
        return messages

    def _ensure_group(self, topic: str) -> None:
        if not self._group:
            return
        try:
            # Create group at "0" so pre-existing entries are delivered to the group
            self._redis.xgroup_create(topic, self._group, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP: Consumer Group name already exists
            msg = str(exc)
            if "BUSYGROUP" in msg or "already exists" in msg:
                return
            # Different error, re-raise
            raise

    @staticmethod
    def _to_bus_message(topic: str, data: dict[str, str], entry_id: str) -> BusMessage:
        payload_raw = data.get("payload", "{}")
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            # Malformed payload JSON – fall back to empty and record a warning/metric
            logger = get_json_logger("magent2.bus")
            logger.warning(
                "invalid bus payload json",
                extra={
                    "event": "bus_payload_invalid_json",
                    "topic": topic,
                    "entry_id": entry_id,
                },
            )
            get_metrics().increment("bus_payload_decode_errors", {"topic": topic})
            payload = {}

        bus_id = data.get("id") or entry_id
        return BusMessage(topic=topic, payload=payload, id=bus_id)


__all__ = ["RedisBus"]
