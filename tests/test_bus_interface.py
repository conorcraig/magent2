from __future__ import annotations

from collections.abc import Iterable

from magent2.bus.interface import Bus, BusMessage


class InMemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
    ) -> Iterable[BusMessage]:
        items = self._topics.get(topic, [])
        if last_id is None:
            return list(items[-limit:])
        # find index of last_id and return after it
        start = 0
        for i, m in enumerate(items):
            if m.id == last_id:
                start = i + 1
                break
        return list(items[start : start + limit])


def test_inmemory_bus_roundtrip() -> None:
    bus = InMemoryBus()
    m1 = BusMessage(topic="chat:conv1", payload={"hello": 1})
    m2 = BusMessage(topic="chat:conv1", payload={"hello": 2})

    id1 = bus.publish("chat:conv1", m1)
    id2 = bus.publish("chat:conv1", m2)
    assert id1 and id2

    out = list(bus.read("chat:conv1", last_id=id1))
    assert len(out) == 1
    assert out[0].id == id2
