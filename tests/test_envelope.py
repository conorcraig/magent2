from __future__ import annotations

from magent2.models.envelope import (
    MessageEnvelope,
    OutputEvent,
    TokenEvent,
    ToolStepEvent,
)


def test_message_envelope_minimal_fields() -> None:
    env = MessageEnvelope(
        conversation_id="conv_1",
        sender="user:conor",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    assert env.id
    assert env.conversation_id == "conv_1"
    assert env.sender == "user:conor"
    assert env.recipient == "agent:DevAgent"
    assert env.type == "message"
    assert env.created_at.tzinfo is not None


def test_stream_events_shapes() -> None:
    t = TokenEvent(conversation_id="conv_1", text="Hi", index=0)
    assert t.event == "token"
    assert t.text == "Hi"
    assert t.index == 0

    s = ToolStepEvent(
        conversation_id="conv_1",
        name="terminal.run",
        args={"cmd": "echo hi"},
        result_summary="ok",
    )
    assert s.event == "tool_step"
    assert s.name == "terminal.run"
    assert s.args["cmd"] == "echo hi"
    assert s.result_summary == "ok"

    o = OutputEvent(conversation_id="conv_1", text="done")
    assert o.event == "output"
    assert o.text == "done"
