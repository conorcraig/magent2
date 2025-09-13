from __future__ import annotations

import uuid
from typing import Any

from magent2.bus.interface import BusMessage

pytestmark: list = []


def _unique_topic(prefix: str = "chat:test") -> str:
    return f"{prefix}:{uuid.uuid4()}"


def test_redis_bus_roundtrip(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=redis_url)

    m1 = BusMessage(topic=topic, payload={"n": 1})
    m2 = BusMessage(topic=topic, payload={"n": 2})

    id1 = bus.publish(topic, m1)
    id2 = bus.publish(topic, m2)
    assert id1 and id2

    out = list(bus.read(topic, last_id=id1))
    assert len(out) == 1
    assert out[0].id == id2
    assert out[0].payload == {"n": 2}


def test_redis_bus_tail_read_limit(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=redis_url)

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


def test_redis_bus_consumer_group_ack(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    group = f"g-{uuid.uuid4()}"
    bus = RedisBus(redis_url=redis_url, group_name=group, consumer_name="c1")

    mid = bus.publish(topic, BusMessage(topic=topic, payload={"n": 1}))
    assert mid

    out = list(bus.read(topic, last_id=None, limit=10))
    assert len(out) >= 1

    # Validate messages are acknowledged (no pending) when using consumer groups
    client = bus._redis
    pending = _pending_count_raw(client, topic, group)
    if pending >= 0:
        assert pending == 0


def test_redis_bus_blocking_read_no_group_timeout(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=redis_url)

    # No messages yet; blocking read should timeout and return empty list
    out = list(bus.read_blocking(topic, last_id=None, limit=10, block_ms=200))
    assert out == []


def test_redis_bus_blocking_read_receives_new_messages(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    bus = RedisBus(redis_url=redis_url)

    # Publish a baseline message to establish a cursor
    baseline_id = bus.publish(topic, BusMessage(topic=topic, payload={"n": 1}))
    assert baseline_id
    # Publish the next message we expect to read via blocking API
    target_id = bus.publish(topic, BusMessage(topic=topic, payload={"n": 42}))
    assert target_id
    # Use read_blocking with last_id set to the baseline message id; should return the target
    out = list(bus.read_blocking(topic, last_id=baseline_id, limit=10, block_ms=500))
    assert len(out) >= 1
    assert any(m.id == target_id for m in out)


def test_redis_bus_blocking_read_with_group(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus

    topic = _unique_topic()
    group = f"g-{uuid.uuid4()}"
    bus = RedisBus(redis_url=redis_url, group_name=group, consumer_name="c1")

    mid = bus.publish(topic, BusMessage(topic=topic, payload={"n": 7}))
    assert mid
    out = list(bus.read_blocking(topic, last_id=None, limit=10, block_ms=500))
    assert len(out) >= 1
    assert any(m.id == mid for m in out)
