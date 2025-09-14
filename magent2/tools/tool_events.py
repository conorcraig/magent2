from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from magent2.tools.signals.impl import _maybe_publish_stream_event


def _clip_text(value: str, limit: int = 160) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "\u2026"


@dataclass(slots=True)
class ToolStepContext:
    name: str
    args: dict[str, Any]
    tool_call_id: str
    start_ns: int
    finished: bool = False

    def success(self, *, result_summary: str | None = None, duration_ms: int | None = None) -> None:
        if self.finished:
            return
        dur = duration_ms
        if dur is None:
            dur = int((time.perf_counter_ns() - self.start_ns) / 1_000_000)
        payload: dict[str, Any] = {
            "event": "tool_step",
            "name": self.name,
            "args": {},
            "status": "success",
            "tool_call_id": self.tool_call_id,
            "duration_ms": int(dur),
        }
        if result_summary:
            payload["result_summary"] = _clip_text(str(result_summary))
        _maybe_publish_stream_event(payload)
        self.finished = True

    def error(self, *, error: str) -> None:
        if self.finished:
            return
        payload: dict[str, Any] = {
            "event": "tool_step",
            "name": self.name,
            "args": {},
            "status": "error",
            "tool_call_id": self.tool_call_id,
            "error": _clip_text(str(error)),
        }
        _maybe_publish_stream_event(payload)
        self.finished = True

    # Context manager protocol
    def __enter__(self) -> ToolStepContext:  # noqa: D401
        _maybe_publish_stream_event(
            {
                "event": "tool_step",
                "name": self.name,
                "args": self.args,
                "status": "start",
                "tool_call_id": self.tool_call_id,
            }
        )
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
    ) -> None:  # noqa: D401
        # Do not auto-complete; callers must call success() or error()
        return None


def tool_step(name: str, *, args: dict[str, Any] | None = None) -> ToolStepContext:
    """Create a tool step context that emits start/success/error with a stable id."""
    return ToolStepContext(
        name=name,
        args=dict(args or {}),
        tool_call_id=f"tc_{uuid.uuid4().hex}",
        start_ns=time.perf_counter_ns(),
    )
