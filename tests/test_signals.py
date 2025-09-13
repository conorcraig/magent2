from __future__ import annotations

import os
import time

from magent2.bus.interface import Bus, BusMessage
from magent2.observability import use_run_context
from magent2.tools.signals import impl as signals_impl
from magent2.tools.signals.impl import (
    send_signal,
    set_bus_for_testing,
    wait_for_signal,
)


class _MemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(self, topic: str, last_id: str | None = None, limit: int = 100) -> list[BusMessage]:
        items = self._topics.get(topic, [])
        if last_id is None:
            return items[-limit:]
        # find by id then return after
        idx = -1
        for i, m in enumerate(items):
            if m.id == last_id:
                idx = i
                break
        if idx == -1:
            return []
        return items[idx + 1 : idx + 1 + limit]

    def read_blocking(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
        block_ms: int = 1000,
    ) -> list[BusMessage]:
        return self.read(topic, last_id=last_id, limit=limit)


def test_signal_send_and_wait_roundtrip() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    try:
        res_send = send_signal("signal:test", {"n": 1})
        assert res_send["ok"] is True
        first_id = res_send["message_id"]

        res_wait = wait_for_signal("signal:test", last_id=None, timeout_ms=10)
        assert res_wait["ok"] is True
        assert res_wait["message"]["event"] == "signal"
        assert res_wait["message"]["payload"] == {"n": 1}
        assert res_wait["message_id"] == first_id
    finally:
        set_bus_for_testing(None)


def test_signal_wait_timeout() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    try:
        res_wait = wait_for_signal("signal:empty", last_id=None, timeout_ms=1)
        assert res_wait["ok"] is False
        assert "timeout_ms" in res_wait
    finally:
        set_bus_for_testing(None)


def test_signal_wait_any_across_topics() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    try:
        # Pre-populate one of the topics
        res1 = send_signal("signal:a", {"x": 1})
        assert res1["ok"] is True

        # Wait for any of the topics to receive a message
        res_wait_any = signals_impl.wait_for_any(
            ["signal:a", "signal:b"], last_ids=None, timeout_ms=50
        )
        assert res_wait_any["ok"] is True
        assert res_wait_any["topic"] == "signal:a"
        assert res_wait_any["message"]["event"] == "signal"
        assert res_wait_any["message"]["payload"] == {"x": 1}
    finally:
        set_bus_for_testing(None)


def test_signal_wait_all_across_topics() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    try:
        # Publish to both topics (staggered)
        res_a = send_signal("signal:all:a", {"a": 1})
        time.sleep(0.01)
        res_b = send_signal("signal:all:b", {"b": 2})
        assert res_a["ok"] and res_b["ok"]

        res_wait_all = signals_impl.wait_for_all(
            ["signal:all:a", "signal:all:b"], last_ids=None, timeout_ms=100
        )
        assert res_wait_all["ok"] is True
        messages = res_wait_all["messages"]
        assert set(messages.keys()) == {"signal:all:a", "signal:all:b"}
        assert messages["signal:all:a"]["message"]["payload"] == {"a": 1}
        assert messages["signal:all:b"]["message"]["payload"] == {"b": 2}
    finally:
        set_bus_for_testing(None)


def test_signal_policy_prefix_denial_and_allow() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    old = os.environ.get("SIGNAL_TOPIC_PREFIX")
    os.environ["SIGNAL_TOPIC_PREFIX"] = "signal:teamA/"
    try:
        # Denied
        try:
            send_signal("signal:teamB/task", {"ok": True})
            assert False, "expected ValueError"
        except ValueError:
            pass
        # Allowed
        res = send_signal("signal:teamA/task", {"ok": True})
        assert res["ok"] is True
    finally:
        if old is None:
            os.environ.pop("SIGNAL_TOPIC_PREFIX", None)
        else:
            os.environ["SIGNAL_TOPIC_PREFIX"] = old
        set_bus_for_testing(None)


def test_signal_payload_cap_rejects() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    old = os.environ.get("SIGNAL_PAYLOAD_MAX_BYTES")
    os.environ["SIGNAL_PAYLOAD_MAX_BYTES"] = "16"
    try:
        big_payload = {"a": "x" * 100}
        try:
            send_signal("signal:test_cap", big_payload)
            assert False, "expected ValueError due to payload size cap"
        except ValueError:
            pass
    finally:
        if old is None:
            os.environ.pop("SIGNAL_PAYLOAD_MAX_BYTES", None)
        else:
            os.environ["SIGNAL_PAYLOAD_MAX_BYTES"] = old
        set_bus_for_testing(None)


def test_signal_sse_visibility_and_redaction() -> None:
    bus = _MemoryBus()
    set_bus_for_testing(bus)
    try:
        conversation_id = "conv_sig_sse"
        stream_topic = f"stream:{conversation_id}"
        with use_run_context("run-1", conversation_id, "agent-x"):
            # Include a sensitive key that should be redacted
            send_signal("signal:sse", {"token": "abcd", "n": 1})
            res = wait_for_signal("signal:sse", last_id=None, timeout_ms=50)
            assert res["ok"] is True
            assert res["message"]["payload"]["token"] == "[REDACTED]"

        # Check that SSE events were published
        events = bus._topics.get(stream_topic, [])
        kinds = [m.payload.get("event") for m in events]
        assert "signal_send" in kinds
        assert "signal_recv" in kinds
    finally:
        set_bus_for_testing(None)
