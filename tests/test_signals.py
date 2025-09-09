from __future__ import annotations

from magent2.bus.interface import Bus, BusMessage
from magent2.tools.signals.impl import send_signal, set_bus_for_testing, wait_for_signal


class _MemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(
        self, topic: str, last_id: str | None = None, limit: int = 100
    ) -> list[BusMessage]:
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
