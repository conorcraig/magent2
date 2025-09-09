from __future__ import annotations

import json
import os
import time
from typing import Any

from magent2.bus.interface import Bus, BusMessage

_TEST_BUS: Bus | None = None
_BUS_CACHE: Bus | None = None


def set_bus_for_testing(bus: Bus | None) -> None:
    global _TEST_BUS, _BUS_CACHE
    _TEST_BUS = bus
    if bus is None:
        _BUS_CACHE = None


def _get_bus() -> Bus:
    if _TEST_BUS is not None:
        return _TEST_BUS
    global _BUS_CACHE
    if _BUS_CACHE is not None:
        return _BUS_CACHE
    from magent2.bus.redis_adapter import RedisBus

    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _BUS_CACHE = RedisBus(url)
    return _BUS_CACHE


def send_signal(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = (topic or "").strip()
    if not name:
        raise ValueError("topic must be non-empty")
    # Normalize payload to a dict json-serializable
    try:
        json.dumps(payload)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("payload must be JSON-serializable") from exc

    bus = _get_bus()
    msg = BusMessage(topic=name, payload={"event": "signal", "payload": payload})
    message_id = bus.publish(name, msg)
    return {"ok": True, "topic": name, "message_id": message_id}


def wait_for_signal(topic: str, *, last_id: str | None, timeout_ms: int) -> dict[str, Any]:
    name = (topic or "").strip()
    if not name:
        raise ValueError("topic must be non-empty")
    if timeout_ms <= 0:
        timeout_ms = 1

    bus = _get_bus()
    deadline = time.time() + (timeout_ms / 1000.0)
    cursor: str | None = last_id
    while True:
        items = list(bus.read(name, last_id=cursor, limit=1))
        if items:
            m = items[0]
            return {"ok": True, "topic": name, "message": m.payload, "message_id": m.id}
        if time.time() >= deadline:
            # Return a structured timeout without raising, so the agent can decide to keep waiting
            return {"ok": False, "topic": name, "timeout_ms": timeout_ms, "last_id": cursor or ""}
        time.sleep(0.05)


__all__ = ["send_signal", "wait_for_signal", "set_bus_for_testing"]
