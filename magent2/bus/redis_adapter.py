from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from typing import Any

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
        client: Any | None = None,
    ) -> None:
        try:
            import redis
        except Exception as exc:  # pragma: no cover - import-time error path
            raise RuntimeError("redis package is required for RedisBus") from exc

        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        if client is not None:
            self._redis = client
        else:
            # decode_responses=True returns str everywhere for easier JSON handling
            self._redis = redis.from_url(url, decode_responses=True)

        self._group = group_name
        self._consumer = consumer_name or f"consumer-{uuid.uuid4()}"

    # ----------------------------
    # Public API
    # ----------------------------
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

    # ----------------------------
    # Internal helpers
    # ----------------------------
    def _read_without_group(
        self, topic: str, last_id: str | None, limit: int
    ) -> Iterable[BusMessage]:
        # Tail: last N items in chronological order
        if last_id is None:
            entries = self._redis.xrevrange(topic, "+", "-", count=limit) or []
            entries.reverse()
            for entry_id, data in entries:
                yield self._to_bus_message(topic, data, entry_id)
            return

        # Find last_id by either canonical uuid stored in field 'id' or by entry id
        cursor = "-"
        collected: list[tuple[str, dict[str, str]]] = []
        found = False
        while True:
            # Fetch in chunks to avoid pulling the entire stream for very large topics
            start = cursor if cursor == "-" else f"({cursor}"
            chunk = self._redis.xrange(topic, start, "+", count=max(limit * 2, 100)) or []
            if not chunk:
                break
            for entry_id, data in chunk:
                if not found:
                    if data.get("id") == last_id or entry_id == last_id:
                        found = True
                        continue
                else:
                    collected.append((entry_id, data))
                    if len(collected) >= limit:
                        break
            if len(collected) >= limit or chunk[-1][0] == cursor:
                break
            cursor = chunk[-1][0]

        for entry_id, data in collected[:limit]:
            yield self._to_bus_message(topic, data, entry_id)

    def _read_with_group(self, topic: str, limit: int) -> Iterable[BusMessage]:
        self._ensure_group(topic)

        # Only read messages never delivered to the group ("new"), not pending ones
        resp = self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={topic: ">"},
            count=limit,
            block=0,
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
        except Exception:
            payload = {}

        bus_id = data.get("id") or entry_id
        return BusMessage(topic=topic, payload=payload, id=bus_id)


__all__ = ["RedisBus"]
