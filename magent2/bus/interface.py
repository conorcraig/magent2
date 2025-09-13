from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class BusMessage:
    topic: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class Bus(Protocol):
    """Minimal pluggable Bus interface.

    Keep this tiny and stable to enable swapping transports without changing callers.
    """

    def publish(self, topic: str, message: BusMessage) -> str:
        """Append one message to a topic. Returns message id."""

    def read(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
    ) -> Iterable[BusMessage]:
        """Read messages after last_id (or tail if None)."""

    def read_blocking(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
        block_ms: int = 1000,
    ) -> Iterable[BusMessage]:
        """Block up to block_ms waiting for messages after last_id.

        Returns an empty iterable on timeout. Implementations should prefer
        native blocking primitives (e.g., Redis XREAD/XREADGROUP with BLOCK).
        """


__all__ = ["Bus", "BusMessage"]
