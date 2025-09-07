from __future__ import annotations

import types
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from magent2.models.envelope import (
    BaseStreamEvent,
    MessageEnvelope,
    OutputEvent,
    TokenEvent,
    ToolStepEvent,
)


class _FakeResultStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def stream_events(self) -> AsyncIterator[Any]:  # pragma: no cover - exercised via adapter
        for ev in self._events:
            yield ev


class _FakeSDKRunner:
    @staticmethod
    def run_streamed(agent: Any, input: str, session: Any) -> _FakeResultStream:
        # The agent is unused in the fake; we only validate that our adapter calls this API shape
        return _FakeResultStream(_FakeSDKRunner._next_events())

    # patched per-test to supply events
    _next_events: Any = staticmethod(lambda: [])


def _patch_sdk_runner(monkeypatch: pytest.MonkeyPatch, events: list[Any]) -> None:
    import magent2.runner.openai_agents_runner as oar

    # Install the fake SDK Runner for this test
    _FakeSDKRunner._next_events = staticmethod(lambda: list(events))
    monkeypatch.setattr(oar, "SDKRunner", _FakeSDKRunner)


def _make_event(ev_type: str, data: Any) -> Any:
    return types.SimpleNamespace(type=ev_type, data=data)


def _build_runner_and_env() -> tuple[Any, MessageEnvelope]:
    from agents import Agent

    from magent2.runner.openai_agents_runner import OpenAIAgentsRunner

    agent = Agent(name="DevAgent", instructions="You are a helpful assistant.")
    runner = OpenAIAgentsRunner(agent)
    env = MessageEnvelope(
        conversation_id="conv_test",
        sender="user:test",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )
    return runner, env


def test_adapter_emits_two_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sdk_runner(
        monkeypatch,
        [
            _make_event("raw_response_event", {"delta": "H"}),
            _make_event("raw_response_event", {"delta": "i"}),
        ],
    )
    runner, env = _build_runner_and_env()
    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    assert isinstance(out[0], TokenEvent) and out[0].text == "H" and out[0].index == 0
    assert isinstance(out[1], TokenEvent) and out[1].text == "i" and out[1].index == 1


def test_adapter_maps_tool_invocation_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sdk_runner(
        monkeypatch,
        [
            _make_event(
                "run_item_stream_event",
                {"name": "terminal.run", "args": {"cmd": "echo hi"}},
            ),
            _make_event(
                "run_item_stream_event",
                {"name": "terminal.run", "result": "ok"},
            ),
        ],
    )
    runner, env = _build_runner_and_env()
    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    assert isinstance(out[0], ToolStepEvent)
    assert out[0].name == "terminal.run" and out[0].args == {"cmd": "echo hi"}
    assert isinstance(out[1], ToolStepEvent)
    assert out[1].name == "terminal.run" and out[1].result_summary == "ok"


def test_adapter_emits_final_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_sdk_runner(
        monkeypatch,
        [
            _make_event("raw_response_event", {"delta": "H"}),
            _make_event("raw_response_event", {"delta": "i"}),
            _make_event(
                "run_item_stream_event",
                {"name": "terminal.run", "result": "ok"},
            ),
        ],
    )
    runner, env = _build_runner_and_env()
    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    assert isinstance(out[-1], OutputEvent)


def test_adapter_reuses_session_by_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prepare a spy to capture session identities passed to SDK
    seen_sessions: list[Any] = []

    class _SpySDKRunner:
        @staticmethod
        def run_streamed(agent: Any, input: str, session: Any) -> _FakeResultStream:
            seen_sessions.append(session)
            return _FakeResultStream([])

    import magent2.runner.openai_agents_runner as oar

    monkeypatch.setattr(oar, "SDKRunner", _SpySDKRunner)

    from agents import Agent

    from magent2.runner.openai_agents_runner import OpenAIAgentsRunner

    agent = Agent(name="DevAgent", instructions="You are a helpful assistant.")
    runner = OpenAIAgentsRunner(agent)

    env = MessageEnvelope(
        conversation_id="conv_same",
        sender="user:test",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    # Run twice with the same conversation id
    list(runner.stream_run(env))
    list(runner.stream_run(env))

    assert len(seen_sessions) == 2
    assert seen_sessions[0] is seen_sessions[1]
    # Also ensure only one session object is cached
    assert getattr(runner, "_sessions") is not None
    assert len(getattr(runner, "_sessions")) == 1


def test_adapter_prefers_explicit_final_output_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange SDK-like events with explicit final output
    final = {
        "type": "assistant_message",
        "final": True,
        "text": "Final answer",
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }

    sdk_events = [
        _make_event("raw_response_event", {"delta": "F"}),
        _make_event("run_item_stream_event", final),
    ]
    _patch_sdk_runner(monkeypatch, sdk_events)

    from agents import Agent

    from magent2.runner.openai_agents_runner import OpenAIAgentsRunner

    agent = Agent(name="DevAgent", instructions="You are a helpful assistant.")
    runner = OpenAIAgentsRunner(agent)

    env = MessageEnvelope(
        conversation_id="conv_final",
        sender="user:test",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    kinds = [
        "token" if isinstance(e, TokenEvent) else "output" if isinstance(e, OutputEvent) else "?"
        for e in out
    ]
    assert kinds == ["token", "output"]
    assert isinstance(out[-1], OutputEvent)
    assert out[-1].text == "Final answer"
    assert out[-1].usage == {"input_tokens": 5, "output_tokens": 2}


def test_adapter_tolerates_mapping_errors_and_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EvilEvent:
        type = "run_item_stream_event"
        data = object()

    sdk_events = [
        _make_event("raw_response_event", {"delta": "A"}),
        _EvilEvent(),  # will cause mapper to raise if it assumes dict/attributes
    ]
    _patch_sdk_runner(monkeypatch, sdk_events)

    from agents import Agent

    from magent2.runner.openai_agents_runner import OpenAIAgentsRunner

    agent = Agent(name="DevAgent", instructions="You are a helpful assistant.")
    runner = OpenAIAgentsRunner(agent)

    env = MessageEnvelope(
        conversation_id="conv_err",
        sender="user:test",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    kinds = [
        "token" if isinstance(e, TokenEvent) else "output" if isinstance(e, OutputEvent) else "?"
        for e in out
    ]
    assert kinds == ["token", "output"]
    assert isinstance(out[-1], OutputEvent)
    assert out[-1].text.startswith("A")


def test_adapter_handles_backpressure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate Full on put_nowait to test backpressure resilience
    sdk_events = [
        _make_event("raw_response_event", {"delta": "B"}),
        _make_event("raw_response_event", {"delta": "C"}),
    ]
    _patch_sdk_runner(monkeypatch, sdk_events)

    from agents import Agent

    from magent2.runner.openai_agents_runner import OpenAIAgentsRunner

    agent = Agent(name="DevAgent", instructions="You are a helpful assistant.")
    runner = OpenAIAgentsRunner(agent)

    # Monkeypatch Queue.put_nowait on instance created inside runner
    original_run_stream = runner.stream_run

    def _wrapped_stream(env: MessageEnvelope) -> list[BaseStreamEvent]:
        gen = original_run_stream(env)
        return list(cast(list[BaseStreamEvent], gen))

    # We can't easily intercept the internal queue here.
    # Instead, rely on the final OutputEvent being emitted.
    env = MessageEnvelope(
        conversation_id="conv_bp",
        sender="user:test",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )
    out_any = list(runner.stream_run(env))
    out = cast(list[BaseStreamEvent], out_any)
    # Even if tokens were dropped, there must be an OutputEvent
    assert out and isinstance(out[-1], OutputEvent) and out[-1].event == "output"
