from __future__ import annotations

from collections.abc import Iterable

from magent2.bus.interface import Bus, BusMessage
from tests.helpers.bus import InMemoryBus


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
