from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import (
    MessageEnvelope,
    OutputEvent,
    TokenEvent,
    ToolStepEvent,
)


class _InMemoryBus(Bus):
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


@dataclass(slots=True)
class _FakeRunner:
    """Deterministic runner that yields predefined events per conversation."""

    events_by_conversation: dict[str, list[Any]] = field(default_factory=dict)

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[Any]:
        # Return a COPY so tests can call runner multiple times if they want
        return list(self.events_by_conversation.get(envelope.conversation_id, []))


def _publish_inbound(bus: Bus, env: MessageEnvelope, agent_name: str) -> None:
    topic = f"chat:{agent_name}"
    bus.publish(topic, BusMessage(topic=topic, payload=env.model_dump()))


def test_worker_streams_events_and_outputs() -> None:
    from magent2.worker.worker import Worker

    bus = _InMemoryBus()

    env = MessageEnvelope(
        conversation_id="conv_42",
        sender="user:conor",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    # Predefine streamed events from the runner
    events = [
        TokenEvent(conversation_id=env.conversation_id, text="H", index=0),
        TokenEvent(conversation_id=env.conversation_id, text="i", index=1),
        ToolStepEvent(
            conversation_id=env.conversation_id,
            name="terminal.run",
            args={"cmd": "echo hi"},
            result_summary="ok",
        ),
        OutputEvent(conversation_id=env.conversation_id, text="done"),
    ]
    runner = _FakeRunner(events_by_conversation={env.conversation_id: events})

    worker = Worker(agent_name="DevAgent", bus=bus, runner=runner)

    _publish_inbound(bus, env, agent_name="DevAgent")

    processed = worker.process_available()
    assert processed == 1

    stream_topic = f"stream:{env.conversation_id}"
    out = list(bus.read(stream_topic))
    assert [m.payload["event"] for m in out] == [
        "token",
        "token",
        "tool_step",
        "output",
    ]
    assert out[-1].payload["text"] == "done"


def test_worker_one_run_per_conversation() -> None:
    from magent2.worker.worker import Worker

    bus = _InMemoryBus()

    env1 = MessageEnvelope(
        conversation_id="conv_abc",
        sender="user:conor",
        recipient="agent:DevAgent",
        type="message",
        content="first",
    )
    env2 = MessageEnvelope(
        conversation_id="conv_abc",
        sender="user:conor",
        recipient="agent:DevAgent",
        type="message",
        content="second",
    )

    # Each run yields two events (token + output)
    runner = _FakeRunner(
        events_by_conversation={
            env1.conversation_id: [
                TokenEvent(conversation_id=env1.conversation_id, text="t", index=0),
                OutputEvent(conversation_id=env1.conversation_id, text="done1"),
            ]
        }
    )

    worker = Worker(agent_name="DevAgent", bus=bus, runner=runner)

    # Publish two messages for the same conversation before processing
    _publish_inbound(bus, env1, agent_name="DevAgent")
    _publish_inbound(bus, env2, agent_name="DevAgent")

    # First drain: should process ONLY one run for the conversation
    processed1 = worker.process_available()
    assert processed1 == 1
    stream_topic = f"stream:{env1.conversation_id}"
    out1 = list(bus.read(stream_topic))
    assert [m.payload["event"] for m in out1] == ["token", "output"]

    # Second drain: should process the queued second message (still no concurrency)
    # Provide events for the second run as well
    runner.events_by_conversation[env2.conversation_id] = [
        TokenEvent(conversation_id=env2.conversation_id, text="t2", index=0),
        OutputEvent(conversation_id=env2.conversation_id, text="done2"),
    ]

    processed2 = worker.process_available()
    assert processed2 == 1
    out2 = list(bus.read(stream_topic))
    assert [m.payload["event"] for m in out2] == [
        "token",
        "output",
        "token",
        "output",
    ]


def test_runner_selection_by_env(monkeypatch):
    # Import helper after patching env each time
    import importlib

    m = importlib.import_module("magent2.worker.__main__")

    # Case 1: no API key -> EchoRunner
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    importlib.reload(m)
    r1 = m.build_runner_from_env()
    assert r1.__class__.__name__ == "EchoRunner"

    # Case 2: api key set -> OpenAIAgentsRunner
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    importlib.reload(m)
    r2 = m.build_runner_from_env()
    assert r2.__class__.__name__ == "OpenAIAgentsRunner"
