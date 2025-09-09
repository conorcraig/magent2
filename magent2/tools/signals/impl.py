from __future__ import annotations

import json
import os
import time
from typing import Any

from magent2.bus.interface import Bus, BusMessage
from magent2.observability import SENSITIVE_KEYS, get_run_context

_TEST_BUS: Bus | None = None
_BUS_CACHE: Bus | None = None
_CURSORS_BY_CONVERSATION: dict[str, dict[str, str]] = {}


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


def _require_allowed_topic(topic: str) -> None:
    prefix = os.getenv("SIGNAL_TOPIC_PREFIX", "").strip()
    if prefix and not topic.startswith(prefix):
        raise ValueError("topic not allowed by prefix policy")


def _payload_cap_bytes() -> int | None:
    raw = os.getenv("SIGNAL_PAYLOAD_MAX_BYTES", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _ensure_payload_within_cap(payload: dict[str, Any]) -> int:
    # Return the serialized length for metrics/SSE, and raise if over cap when configured
    payload_str = json.dumps(payload, separators=(",", ":"))
    cap = _payload_cap_bytes()
    if cap is not None and len(payload_str.encode("utf-8")) > cap:
        raise ValueError("payload too large for configured cap")
    return len(payload_str.encode("utf-8"))


def _redact(obj: Any) -> Any:
    # Minimal recursive redaction based on SENSITIVE_KEYS
    from collections.abc import Mapping

    if isinstance(obj, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = _redact(v)
        return redacted
    if isinstance(obj, list | tuple):
        return [_redact(v) for v in obj]
    return obj


def _redacted_signal_message(message_payload: dict[str, Any]) -> dict[str, Any]:
    # Copy shallowly and redact inner payload keys
    redacted = dict(message_payload)
    inner = message_payload.get("payload")
    if isinstance(inner, dict):
        redacted["payload"] = _redact(inner)
    return redacted


def _maybe_get_conversation_id() -> str | None:
    ctx = get_run_context() or {}
    if isinstance(ctx, dict):
        value = ctx.get("conversation_id")
        if value is not None:
            s = str(value).strip()
            if s:
                return s
    return None


def _maybe_publish_stream_event(event: dict[str, Any]) -> None:
    conversation_id = _maybe_get_conversation_id()
    if not conversation_id:
        return
    try:
        bus = _get_bus()
        stream_topic = f"stream:{conversation_id}"
        bus.publish(stream_topic, BusMessage(topic=stream_topic, payload=event))
    except Exception:
        # Best effort: never fail core signal paths due to stream publish
        pass


def _get_persisted_cursor(topic: str) -> str | None:
    conversation_id = _maybe_get_conversation_id()
    if not conversation_id:
        return None
    return _CURSORS_BY_CONVERSATION.get(conversation_id, {}).get(topic)


def _set_persisted_cursor(topic: str, last_id: str) -> None:
    conversation_id = _maybe_get_conversation_id()
    if not conversation_id:
        return
    slot = _CURSORS_BY_CONVERSATION.setdefault(conversation_id, {})
    slot[topic] = last_id


def send_signal(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = (topic or "").strip()
    if not name:
        raise ValueError("topic must be non-empty")
    _require_allowed_topic(name)
    # Normalize payload to a dict json-serializable
    try:
        json.dumps(payload)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("payload must be JSON-serializable") from exc

    bus = _get_bus()
    # Enforce payload cap (if configured) before publish
    payload_len = _ensure_payload_within_cap(payload)
    msg = BusMessage(topic=name, payload={"event": "signal", "payload": payload})
    message_id = bus.publish(name, msg)
    # SSE visibility (best effort)
    _maybe_publish_stream_event(
        {
            "event": "signal_send",
            "topic": name,
            "message_id": message_id,
            "payload_len": payload_len,
        }
    )
    return {"ok": True, "topic": name, "message_id": message_id}


def wait_for_signal(topic: str, *, last_id: str | None, timeout_ms: int) -> dict[str, Any]:
    name = (topic or "").strip()
    if not name:
        raise ValueError("topic must be non-empty")
    _require_allowed_topic(name)
    if timeout_ms <= 0:
        timeout_ms = 1

    bus = _get_bus()
    deadline = time.time() + (timeout_ms / 1000.0)
    cursor: str | None = last_id if last_id is not None else _get_persisted_cursor(name)
    while True:
        items = list(bus.read(name, last_id=cursor, limit=1))
        if items:
            m = items[0]
            # Redact payload before returning
            message_payload = m.payload
            redacted = _redacted_signal_message(message_payload)
            # Persist cursor for this topic (if context available)
            _set_persisted_cursor(name, m.id)
            # SSE visibility (best effort)
            try:
                payload_len = len(
                    json.dumps(message_payload, separators=(",", ":")).encode("utf-8")
                )
            except Exception:
                payload_len = 0
            _maybe_publish_stream_event(
                {
                    "event": "signal_recv",
                    "topic": name,
                    "message_id": m.id,
                    "payload_len": payload_len,
                }
            )
            return {"ok": True, "topic": name, "message": redacted, "message_id": m.id}
        if time.time() >= deadline:
            # Return a structured timeout without raising, so the agent can decide to keep waiting
            return {"ok": False, "topic": name, "timeout_ms": timeout_ms, "last_id": cursor or ""}
        time.sleep(0.05)


def wait_for_any(
    topics: list[str], *, last_ids: dict[str, str] | None, timeout_ms: int
) -> dict[str, Any]:
    names = [(t or "").strip() for t in topics]
    names = [n for n in names if n]
    if not names:
        raise ValueError("topics must be non-empty")
    for n in names:
        _require_allowed_topic(n)
    if timeout_ms <= 0:
        timeout_ms = 1
    bus = _get_bus()
    deadline = time.time() + (timeout_ms / 1000.0)
    cursors: dict[str, str | None] = {}
    for n in names:
        explicit = (last_ids or {}).get(n) if last_ids else None
        cursors[n] = explicit if explicit is not None else _get_persisted_cursor(n)
    while True:
        for n in names:
            items = list(bus.read(n, last_id=cursors[n], limit=1))
            if not items:
                continue
            m = items[0]
            message_payload = m.payload
            redacted = _redacted_signal_message(message_payload)
            _set_persisted_cursor(n, m.id)
            try:
                payload_len = len(
                    json.dumps(message_payload, separators=(",", ":")).encode("utf-8")
                )
            except Exception:
                payload_len = 0
            _maybe_publish_stream_event(
                {
                    "event": "signal_recv",
                    "topic": n,
                    "message_id": m.id,
                    "payload_len": payload_len,
                }
            )
            return {"ok": True, "topic": n, "message": redacted, "message_id": m.id}
        if time.time() >= deadline:
            return {"ok": False, "topics": names, "timeout_ms": timeout_ms}
        time.sleep(0.05)


def wait_for_all(
    topics: list[str], *, last_ids: dict[str, str] | None, timeout_ms: int
) -> dict[str, Any]:
    names = [(t or "").strip() for t in topics]
    names = [n for n in names if n]
    if not names:
        raise ValueError("topics must be non-empty")
    for n in names:
        _require_allowed_topic(n)
    if timeout_ms <= 0:
        timeout_ms = 1
    bus = _get_bus()
    deadline = time.time() + (timeout_ms / 1000.0)
    cursors: dict[str, str | None] = {}
    results: dict[str, dict[str, Any]] = {}
    for n in names:
        explicit = (last_ids or {}).get(n) if last_ids else None
        cursors[n] = explicit if explicit is not None else _get_persisted_cursor(n)
    while True:
        remaining = [n for n in names if n not in results]
        for n in remaining:
            items = list(bus.read(n, last_id=cursors[n], limit=1))
            if not items:
                continue
            m = items[0]
            message_payload = m.payload
            redacted = _redacted_signal_message(message_payload)
            _set_persisted_cursor(n, m.id)
            try:
                payload_len = len(
                    json.dumps(message_payload, separators=(",", ":")).encode("utf-8")
                )
            except Exception:
                payload_len = 0
            _maybe_publish_stream_event(
                {
                    "event": "signal_recv",
                    "topic": n,
                    "message_id": m.id,
                    "payload_len": payload_len,
                }
            )
            results[n] = {"ok": True, "topic": n, "message": redacted, "message_id": m.id}
        if len(results) == len(names):
            return {"ok": True, "messages": results}
        if time.time() >= deadline:
            return {"ok": False, "messages": results, "timeout_ms": timeout_ms}
        time.sleep(0.05)


__all__ = [
    "send_signal",
    "wait_for_signal",
    "wait_for_any",
    "wait_for_all",
    "set_bus_for_testing",
]
