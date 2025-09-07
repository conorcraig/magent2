from __future__ import annotations

from collections.abc import Iterable

import pytest

from magent2.bus.interface import Bus, BusMessage
from magent2.tools.chat.function_tools import send_message, set_bus_for_testing


class InMemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(
        self, topic: str, last_id: str | None = None, limit: int = 100
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


@pytest.fixture()
def bus() -> InMemoryBus:
    b = InMemoryBus()
    set_bus_for_testing(b)
    return b


def _last_message(bus: InMemoryBus, topic: str) -> BusMessage:
    messages = list(bus.read(topic, last_id=None, limit=1_000))
    assert messages, "no messages on topic"
    return messages[-1]


def test_publish_to_conversation_topic_for_chat_recipient(
    bus: InMemoryBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure deterministic sender
    monkeypatch.setenv("AGENT_NAME", "Tester")

    result = send_message("chat:conv1", "hello world")
    assert result["ok"] is True
    topics = result["published_to"]
    assert topics == ["chat:conv1"]

    last = _last_message(bus, "chat:conv1")
    payload = last.payload
    assert payload["conversation_id"] == "conv1"
    assert payload["recipient"] == "chat:conv1"
    assert payload["type"] == "message"
    assert isinstance(payload["created_at"], str)


def test_publish_to_conversation_and_agent_topics_for_agent_recipient(
    bus: InMemoryBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_NAME", "Tester")
    monkeypatch.setenv("CHAT_TOOL_CONVERSATION_ID", "conv2")

    result = send_message("agent:DevAgent", "ping")
    assert result["ok"] is True
    topics = result["published_to"]
    assert set(topics) == {"chat:conv2", "chat:DevAgent"}

    last_conv = _last_message(bus, "chat:conv2")
    payload = last_conv.payload
    assert payload["conversation_id"] == "conv2"
    assert payload["recipient"] == "agent:DevAgent"

    last_agent = _last_message(bus, "chat:DevAgent")
    assert last_agent.payload == payload


@pytest.mark.parametrize("bad_recipient", ["room:123", "agent:", "", " "])
def test_invalid_recipient_raises_value_error(bus: InMemoryBus, bad_recipient: str) -> None:
    with pytest.raises(ValueError):
        send_message(bad_recipient, "hi")


@pytest.mark.parametrize("bad_content", ["", " "])
def test_blank_content_raises_value_error(bus: InMemoryBus, bad_content: str) -> None:
    with pytest.raises(ValueError):
        send_message("chat:convX", bad_content)
