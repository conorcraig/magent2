from __future__ import annotations

import os
from typing import Any

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import OutputEvent
from magent2.tools.chat.function_tools import set_bus_for_testing as set_chat_bus
from magent2.tools.orchestrate import orchestrate_split
from magent2.tools.signals.impl import set_bus_for_testing as set_signal_bus


class _MemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(self, topic: str, last_id: str | None = None, limit: int = 100):
        items = self._topics.get(topic, [])
        if last_id is None:
            return items[-limit:]
        idx = -1
        for i, m in enumerate(items):
            if m.id == last_id:
                idx = i
                break
        if idx == -1:
            return []
        return items[idx + 1 : idx + 1 + limit]


def test_orchestrate_split_metadata_and_topics(monkeypatch):
    bus = _MemoryBus()
    set_chat_bus(bus)
    set_signal_bus(bus)
    try:
        monkeypatch.setenv("AGENT_NAME", "DevAgent")
        res = orchestrate_split(
            "Do work",
            num_children=2,
            responsibilities=["a"],
            allowed_paths=["src/"],
            wait=False,
            target_agent="AgentX",
            timeout_ms=1000,
        )
        assert res["ok"] is True
        assert len(res["children"]) == 2
        assert all(t.startswith("signal:") and t.endswith(":done") for t in res["topics"])  # type: ignore[index]

        # Check that messages were published to the agent topic with structured metadata
        agent_topic = "chat:AgentX"
        msgs = bus._topics.get(agent_topic, [])
        assert len(msgs) == 2
        for m in msgs:
            env = m.payload
            meta = env.get("metadata", {})
            orch = meta.get("orchestrate", {})
            assert orch.get("responsibilities") == ["a"]
            assert orch.get("allowed_paths") == ["src/"]
            assert isinstance(orch.get("done_topic"), str)
    finally:
        set_chat_bus(None)
        set_signal_bus(None)


def test_worker_auto_signal_done(monkeypatch):
    # Ensure worker reads done_topic from metadata and sends signal
    from magent2.worker.worker import Worker
    from magent2.models.envelope import MessageEnvelope

    bus = _MemoryBus()
    set_signal_bus(bus)

    try:
        monkeypatch.setenv("AUTO_CHILD_SIGNAL_DONE", "1")
        worker = Worker(agent_name="DevAgent", bus=bus, runner=_NoopRunner())
        conv = "conv-child-1234"
        done_topic = f"signal:{conv}:done"
        env = MessageEnvelope(
            conversation_id=conv,
            sender="agent:parent",
            recipient="agent:DevAgent",
            type="message",
            content="whatever",
            metadata={
                "orchestrate": {
                    "responsibilities": ["x"],
                    "allowed_paths": ["."],
                    "done_topic": done_topic,
                }
            },
        )
        # Publish inbound to agent topic
        bus.publish(f"chat:DevAgent", BusMessage(topic=f"chat:DevAgent", payload=env.model_dump()))
        processed = worker.process_available()
        assert processed == 1
        # Verify signal was emitted
        out = bus._topics.get(done_topic, [])
        assert len(out) == 1
        assert out[0].payload.get("event") == "signal"
    finally:
        set_signal_bus(None)


class _NoopRunner:
    def stream_run(self, envelope) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
        # Emit a minimal output event to exercise the stream path
        return [OutputEvent(conversation_id=envelope.conversation_id, text="ok").model_dump(mode="json")]

