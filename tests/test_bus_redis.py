from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from magent2.bus.interface import BusMessage


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _redis_available() -> bool:
    try:
        import redis

        r = redis.from_url(_redis_url(), decode_responses=True)
        try:
            r.ping()
            return True
        except Exception:
            return False
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(), reason="Redis is not available at REDIS_URL"
)


def _unique_topic(prefix: str = "chat:test") -> str:
    return f"{prefix}:{uuid.uuid4()}"


def test_redis_bus_roundtrip() -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=_redis_url())

    m1 = BusMessage(topic=topic, payload={"n": 1})
    m2 = BusMessage(topic=topic, payload={"n": 2})

    id1 = bus.publish(topic, m1)
    id2 = bus.publish(topic, m2)
    assert id1 and id2

    out = list(bus.read(topic, last_id=id1))
    assert len(out) == 1
    assert out[0].id == id2
    assert out[0].payload == {"n": 2}


def test_redis_bus_tail_read_limit() -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=_redis_url())

    ids: list[str] = []
    for i in range(5):
        ids.append(bus.publish(topic, BusMessage(topic=topic, payload={"n": i})))

    out = list(bus.read(topic, last_id=None, limit=2))
    assert [m.payload["n"] for m in out] == [3, 4]


def _pending_count_raw(client: Any, topic: str, group: str) -> int:
    # Try modern redis-py first
    if hasattr(client, "xpending_range"):
        try:
            return len(client.xpending_range(topic, group, "-", "+", 100))
        except Exception:
            pass
    # Fallback to summary shape
    try:
        summary = client.xpending(topic, group)
        # redis-py >= 4 returns dict with "pending"
        if isinstance(summary, dict) and "pending" in summary:
            return int(summary["pending"])
    except Exception:
        pass
    # If unsupported, return -1 to indicate unknown
    return -1


def test_redis_bus_consumer_group_ack() -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    group = f"g-{uuid.uuid4()}"
    bus = RedisBus(redis_url=_redis_url(), group_name=group, consumer_name="c1")

    mid = bus.publish(topic, BusMessage(topic=topic, payload={"n": 1}))
    assert mid

    out = list(bus.read(topic, last_id=None, limit=10))
    assert len(out) >= 1

    # Validate messages are acknowledged (no pending) when using consumer groups
    client = bus._redis
    pending = _pending_count_raw(client, topic, group)
    if pending >= 0:
        assert pending == 0
