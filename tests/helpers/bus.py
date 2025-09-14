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
        start = 0
        for i, m in enumerate(items):
            if m.id == last_id:
                start = i + 1
                break
        return list(items[start : start + limit])

    def read_blocking(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
        block_ms: int = 1000,
    ) -> Iterable[BusMessage]:
        return self.read(topic, last_id=last_id, limit=limit)


__all__ = ["InMemoryBus"]
