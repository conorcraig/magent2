from __future__ import annotations

import json
import os
import time
from typing import Any

from magent2.bus.interface import Bus, BusMessage
from magent2.observability import SENSITIVE_KEYS, get_run_context, redact

_TEST_BUS: Bus | None = None
_BUS_CACHE: Bus | None = None
_CURSORs_BY_CONVERSATION: dict[str, dict[str, str]] = {}


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
    # Default to 64KB when not configured
    if not raw:
        return 64 * 1024
    try:
        value = int(raw)
        return value if value > 0 else 64 * 1024
    except Exception:
        # On invalid value, fall back to default
        return 64 * 1024


def _ensure_payload_within_cap(payload: dict[str, Any]) -> int:
    # Return the serialized length for metrics/SSE, and raise if over cap when configured
    payload_str = json.dumps(payload, separators=(",", ":"))
    cap = _payload_cap_bytes()
    size = len(payload_str.encode("utf-8"))
    if cap is not None and size > cap:
        raise ValueError("payload too large for configured cap")
    return size


def _redact(obj: Any) -> Any:
    # Delegate to shared redact for consistency
    return redact(obj)


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
    return _CURSORs_BY_CONVERSATION.get(conversation_id, {}).get(topic)


def _set_persisted_cursor(topic: str, last_id: str) -> None:
    conversation_id = _maybe_get_conversation_id()
    if not conversation_id:
        return
    slot = _CURSORs_BY_CONVERSATION.setdefault(conversation_id, {})
    slot[topic] = last_id


def _prepare_topic_names(topics: list[str]) -> list[str]:
    """Normalize, validate and enforce topic policy for a list of topics.

    Ensures topics are stripped, non-empty, and allowed by prefix policy.
    """
    names = [(t or "").strip() for t in topics]
    names = [n for n in names if n]
    if not names:
        raise ValueError("topics must be non-empty")
    for name in names:
        _require_allowed_topic(name)
    return names


def _fix_timeout_ms(timeout_ms: int) -> int:
    """Ensure a minimum positive timeout in milliseconds."""
    return timeout_ms if timeout_ms > 0 else 1


def _deadline_from_timeout_ms(timeout_ms: int) -> float:
    """Compute an absolute deadline time from a millisecond timeout."""
    return time.time() + (timeout_ms / 1000.0)


def _build_cursors(names: list[str], last_ids: dict[str, str] | None) -> dict[str, str | None]:
    """Resolve initial cursors from explicit last_ids or persisted cursors."""
    cursors: dict[str, str | None] = {}
    for name in names:
        explicit = (last_ids or {}).get(name) if last_ids else None
        cursors[name] = explicit if explicit is not None else _get_persisted_cursor(name)
    return cursors


def _safe_payload_len(payload: dict[str, Any]) -> int:
    """Best-effort serialized payload length for metrics/SSE."""
    try:
        return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except Exception:  # pragma: no cover - defensive
        return 0


def _publish_signal_recv(topic: str, message_id: str, payload_len: int) -> None:
    _maybe_publish_stream_event(
        {
            "event": "signal_recv",
            "topic": topic,
            "message_id": message_id,
            "payload_len": payload_len,
        }
    )


def _process_message_for_return(topic: str, message: BusMessage) -> dict[str, Any]:
    """Redact payload, persist cursor, publish SSE and return standardized dict."""
    message_payload = message.payload
    redacted = _redacted_signal_message(message_payload)
    _set_persisted_cursor(topic, message.id)
    payload_len = _safe_payload_len(message_payload)
    _publish_signal_recv(topic, message.id, payload_len)
    return {"ok": True, "topic": topic, "message": redacted, "message_id": message.id}


def _read_one(bus: Bus, topic: str, cursor: str | None) -> BusMessage | None:
    items = list(bus.read(topic, last_id=cursor, limit=1))
    return items[0] if items else None


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


def _try_fast_path(name: str, cursor: str | None, bus: Bus) -> dict[str, Any] | None:
    """Try non-blocking read first. Return message if available, None otherwise."""
    items = list(bus.read(name, last_id=cursor, limit=1))
    if not items:
        return None

    m = items[0]
    message_payload = m.payload
    redacted = _redacted_signal_message(message_payload)
    _set_persisted_cursor(name, m.id)

    try:
        payload_len = len(json.dumps(message_payload, separators=(",", ":")).encode("utf-8"))
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


def _try_blocking_read(
    name: str, cursor: str | None, remaining_ms: int, bus: Bus
) -> dict[str, Any] | None:
    """Try blocking read if supported. Return message if available, None on timeout."""
    read_blocking_one = getattr(bus, "read_blocking_one", None)
    if not callable(read_blocking_one) or remaining_ms <= 0:
        return None

    msg = read_blocking_one(name, cursor, remaining_ms)
    if msg is not None:
        return _process_message_for_return(name, msg)
    return None


def _poll_with_timeout(name: str, cursor: str | None, deadline: float, bus: Bus) -> dict[str, Any]:
    """Poll for messages until deadline. Return timeout result if no message found."""
    while True:
        if time.time() >= deadline:
            return {"ok": False, "topic": name, "timeout_ms": 0, "last_id": cursor or ""}
        items = list(bus.read(name, last_id=cursor, limit=1))
        if items:
            return _process_message_for_return(name, items[0])
        time.sleep(0.05)


def wait_for_signal(topic: str, *, last_id: str | None, timeout_ms: int) -> dict[str, Any]:
    name = (topic or "").strip()
    if not name:
        raise ValueError("topic must be non-empty")
    _require_allowed_topic(name)
    timeout_ms = _fix_timeout_ms(timeout_ms)

    bus = _get_bus()
    deadline = _deadline_from_timeout_ms(timeout_ms)
    cursor: str | None = last_id if last_id is not None else _get_persisted_cursor(name)

    # Fast path: try a non-blocking read first
    result = _try_fast_path(name, cursor, bus)
    if result:
        return result

    # If supported by the Bus (Redis), use a blocking read for the remaining time
    remaining_ms = max(1, int((deadline - time.time()) * 1000))
    result = _try_blocking_read(name, cursor, remaining_ms, bus)
    if result:
        return result

    # Check if we timed out during blocking read attempt
    if time.time() >= deadline:
        return {"ok": False, "topic": name, "timeout_ms": timeout_ms, "last_id": cursor or ""}

    # Fallback polling when blocking is unavailable
    return _poll_with_timeout(name, cursor, deadline, bus)


def wait_for_any(
    topics: list[str], *, last_ids: dict[str, str] | None, timeout_ms: int
) -> dict[str, Any]:
    names = _prepare_topic_names(topics)
    timeout_ms = _fix_timeout_ms(timeout_ms)
    bus = _get_bus()
    deadline = _deadline_from_timeout_ms(timeout_ms)
    cursors = _build_cursors(names, last_ids)

    # Non-blocking sweep first
    for name in names:
        message = _read_one(bus, name, cursors[name])
        if message is not None:
            return _process_message_for_return(name, message)

    # Blocking read across streams if supported
    read_any_blocking = getattr(bus, "read_any_blocking", None)
    remaining_ms = max(1, int((deadline - time.time()) * 1000))
    if callable(read_any_blocking) and remaining_ms > 0:
        result = read_any_blocking(names, cursors, remaining_ms)
        if result is not None:
            blk_name, blk_msg = result
            return _process_message_for_return(blk_name, blk_msg)
        return {"ok": False, "topics": names, "timeout_ms": timeout_ms}

    # Fallback polling
    while True:
        for name in names:
            message = _read_one(bus, name, cursors[name])
            if message is not None:
                return _process_message_for_return(name, message)
        if time.time() >= deadline:
            return {"ok": False, "topics": names, "timeout_ms": timeout_ms}
        time.sleep(0.05)


def _collect_initial_messages(
    names: list[str], cursors: dict[str, str | None], bus: Bus
) -> dict[str, dict[str, Any]]:
    """Collect any immediately available messages from all topics."""
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        message = _read_one(bus, name, cursors[name])
        if message is not None:
            results[name] = _process_message_for_return(name, message)
    return results


def _try_blocking_read_all(
    remaining: list[str], cursors: dict[str, str | None], remaining_ms: int, bus: Bus
) -> tuple[str, BusMessage] | None:
    """Try blocking read across multiple topics if supported."""
    read_any_blocking = getattr(bus, "read_any_blocking", None)
    if not callable(read_any_blocking) or remaining_ms <= 0:
        return None
    return read_any_blocking(remaining, {k: cursors.get(k) for k in remaining}, remaining_ms)


def _check_recent_arrivals(
    remaining: list[str], cursors: dict[str, str | None], bus: Bus
) -> tuple[str, dict[str, Any]] | None:
    """Check for any recent messages on remaining topics."""
    for name in remaining:
        message = _read_one(bus, name, cursors[name])
        if message is not None:
            return name, _process_message_for_return(name, message)
    return None


def _accumulate_remaining_messages(
    names: list[str], cursors: dict[str, str | None], deadline: float, bus: Bus
) -> dict[str, dict[str, Any]]:
    """Accumulate messages for remaining topics until deadline or all received."""
    results: dict[str, dict[str, Any]] = {}

    while time.time() < deadline and len(results) < len(names):
        remaining = [n for n in names if n not in results]

        # Try to get a message through various methods
        message_result = _try_get_next_message(remaining, cursors, deadline, bus)
        if message_result:
            name, message = message_result
            results[name] = message
            if len(results) == len(names):
                break
        else:
            # No message available, wait before retrying
            if time.time() < deadline:
                time.sleep(0.05)

    return results


def _try_get_next_message(
    remaining: list[str], cursors: dict[str, str | None], deadline: float, bus: Bus
) -> tuple[str, dict[str, Any]] | None:
    """Try various methods to get the next available message."""
    # Check for recent arrivals first
    recent = _check_recent_arrivals(remaining, cursors, bus)
    if recent:
        return recent

    # Try blocking read across remaining topics
    remaining_ms = max(1, int((deadline - time.time()) * 1000))
    blocking_result = _try_blocking_read_all(remaining, cursors, remaining_ms, bus)
    if blocking_result:
        blk_name, blk_msg = blocking_result
        return blk_name, _process_message_for_return(blk_name, blk_msg)

    return None


def wait_for_all(
    topics: list[str], *, last_ids: dict[str, str] | None, timeout_ms: int
) -> dict[str, Any]:
    names = _prepare_topic_names(topics)
    timeout_ms = _fix_timeout_ms(timeout_ms)
    bus = _get_bus()
    deadline = _deadline_from_timeout_ms(timeout_ms)
    cursors = _build_cursors(names, last_ids)

    # Initial non-blocking sweep
    results = _collect_initial_messages(names, cursors, bus)
    if len(results) == len(names):
        return {"ok": True, "messages": results}

    # Accumulate remaining messages
    remaining_results = _accumulate_remaining_messages(names, cursors, deadline, bus)
    results.update(remaining_results)

    return {"ok": len(results) == len(names), "messages": results, "timeout_ms": timeout_ms}


__all__ = [
    "send_signal",
    "wait_for_signal",
    "wait_for_any",
    "wait_for_all",
    "set_bus_for_testing",
]
