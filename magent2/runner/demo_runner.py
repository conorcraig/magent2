from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from magent2.models.envelope import BaseStreamEvent, MessageEnvelope, OutputEvent, ToolStepEvent
from magent2.tools.terminal.function_tools import terminal_run


class DemoRunner:
    """Deterministic runner for local demos without external API calls.

    Protocol: if the message content starts with "run:" the remainder is treated
    as a shell command and executed via the local TerminalTool policy.
    A tool_step event is emitted before/after the execution, followed by a final output.
    """

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        text = (envelope.content or "").strip()
        if text.lower().startswith("run:"):
            cmd = text.split(":", 1)[1].strip()
            # Emit a tool invocation event
            yield ToolStepEvent(
                conversation_id=envelope.conversation_id,
                name="terminal.run",
                args={"command": cmd},
            )
            # Execute via terminal tool wrapper (enforces allowlist/timeouts)
            result = terminal_run(cmd)
            # Emit a tool result summary
            yield ToolStepEvent(
                conversation_id=envelope.conversation_id,
                name="terminal.run",
                args={},
                result_summary=(result[:200] if isinstance(result, str) else str(result)[:200]),
            )
            # Final answer includes the concise result string
            yield OutputEvent(conversation_id=envelope.conversation_id, text=result)
            return
        # Default: echo the content
        yield OutputEvent(conversation_id=envelope.conversation_id, text=text)


__all__ = ["DemoRunner"]

