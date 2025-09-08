from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pytest

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import MessageEnvelope, OutputEvent, TokenEvent
from magent2.observability import (
    get_json_logger,
    get_metrics,
    reset_metrics,
    use_run_context,
)


def _parse_json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.strip().splitlines() if line.strip()]


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
    events_by_conversation: dict[str, list[Any]] = field(default_factory=dict)

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[Any]:
        return list(self.events_by_conversation.get(envelope.conversation_id, []))


def _publish_inbound(bus: Bus, env: MessageEnvelope, agent_name: str) -> None:
    topic = f"chat:{agent_name}"
    bus.publish(topic, BusMessage(topic=topic, payload=env.model_dump()))


def test_worker_logs_include_ids_and_counters_increment(capsys: Any) -> None:
    from magent2.worker.worker import Worker

    reset_metrics()
    # Re-bind handler to current stdout so capsys captures logs even if another test
    # previously initialized the logger before capture began.
    logger = get_json_logger("magent2")
    for h in list(logger.handlers):
        try:
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        except Exception:
            pass
    logger = get_json_logger("magent2")
    logger.setLevel(20)

    bus = _InMemoryBus()
    env = MessageEnvelope(
        conversation_id="conv_obs",
        sender="user:alice",
        recipient="agent:DevAgent",
        type="message",
        content="hi",
    )
    events = [
        TokenEvent(conversation_id=env.conversation_id, text="H", index=0),
        OutputEvent(conversation_id=env.conversation_id, text="done"),
    ]
    runner = _FakeRunner(events_by_conversation={env.conversation_id: events})
    worker = Worker(agent_name="DevAgent", bus=bus, runner=runner)

    _publish_inbound(bus, env, agent_name="DevAgent")

    _ = worker.process_available()

    out = capsys.readouterr().out
    recs = _parse_json_lines(out)
    # Find start and completed events
    starts = [r for r in recs if r.get("event") == "run_started"]
    comps = [r for r in recs if r.get("event") == "run_completed"]
    assert starts and comps
    s = starts[-1]
    c = comps[-1]
    assert s.get("conversation_id") == env.conversation_id
    assert c.get("conversation_id") == env.conversation_id
    assert s.get("agent") == "DevAgent"
    assert c.get("agent") == "DevAgent"
    # run_id should be present and consistent
    assert s.get("run_id") and c.get("run_id") and s["run_id"] == c["run_id"]

    # Metrics should reflect one started and one completed run
    snap = get_metrics().snapshot()
    started = [e for e in snap if e["name"] == "runs_started"]
    completed = [e for e in snap if e["name"] == "runs_completed"]
    assert any(
        e["labels"].get("conversation_id") == env.conversation_id
        and e["labels"].get("agent") == "DevAgent"
        and e["value"] >= 1
        for e in started
    )
    assert any(
        e["labels"].get("conversation_id") == env.conversation_id
        and e["labels"].get("agent") == "DevAgent"
        and e["value"] >= 1
        for e in completed
    )


def test_tool_logs_include_ids_and_tool_counters(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    # Allow running 'echo' in terminal tool
    monkeypatch.setenv("TERMINAL_ALLOWED_COMMANDS", "echo")
    monkeypatch.setenv("TERMINAL_FUNCTION_OUTPUT_MAX_CHARS", "200")

    reset_metrics()
    # Ensure the tools logger binds a fresh StreamHandler to current stdout
    tools_logger = get_json_logger("magent2.tools")
    for h in list(tools_logger.handlers):
        try:
            tools_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        except Exception:
            pass
    tools_logger = get_json_logger("magent2.tools")
    tools_logger.setLevel(20)

    # Establish a run context so logs are enriched
    run_id = str(uuid.uuid4())
    with use_run_context(run_id, conversation_id="conv_tool", agent="DevAgent"):
        from magent2.tools.terminal.function_tools import terminal_run

        _ = terminal_run("echo hi")

    out = capsys.readouterr().out
    recs = _parse_json_lines(out)
    calls = [r for r in recs if r.get("event") == "tool_call" and r.get("tool") == "terminal.run"]
    assert calls, "expected a tool_call log record"
    rec = calls[-1]
    assert rec.get("conversation_id") == "conv_tool"
    assert rec.get("run_id") == run_id

    snap = get_metrics().snapshot()
    tool_calls = [
        e for e in snap if e["name"] == "tool_calls" and e["labels"].get("tool") == "terminal"
    ]
    assert tool_calls and tool_calls[0]["value"] >= 1
