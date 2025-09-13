from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any, cast

from magent2.tools.chat.function_tools import send_message


def _resolve_target_agent(target_agent: str | None) -> str:
    explicit = (target_agent or "").strip()
    if explicit:
        return explicit
    env_override = os.getenv("ORCHESTRATE_TARGET_AGENT", "").strip()
    if env_override:
        return env_override
    env_default = os.getenv("AGENT_NAME", "").strip()
    return env_default or "DevAgent"


def _build_metadata(
    done_topic: str, responsibilities: list[str] | None, allowed_paths: list[str] | None
) -> dict[str, Any]:
    return {
        "orchestrate": {
            "responsibilities": list(responsibilities or []),
            "allowed_paths": list(allowed_paths or []),
            "done_topic": done_topic,
        }
    }


def _dispatch_subtask(
    send_fn: Callable[..., Any], agent: str, task: str, conv_id: str, metadata: dict[str, Any]
) -> None:
    send_fn(f"agent:{agent}", f"Subtask for: {task}", conversation_id=conv_id, metadata=metadata)


def _maybe_wait(
    signal_wait_all_fn: Callable[..., Any], topics: list[str], timeout_ms: int
) -> dict[str, Any]:
    return signal_wait_all_fn(topics, last_ids=None, timeout_ms=int(timeout_ms))


def orchestrate_split(
    task: str,
    num_children: int = 2,
    *,
    responsibilities: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    wait: bool = False,
    target_agent: str | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Split a task across N child agents and return child conversation info.

    Note: This implementation does not block on completion even if `wait=True`.
    """
    n = max(0, int(num_children))
    conv_ids: list[str] = []
    topics: list[str] = []

    resolved_target = _resolve_target_agent(target_agent)

    # mypy: imported symbols may be FunctionTools; treat as callables at runtime
    send_message_fn = cast(Callable[..., Any], send_message)

    for _ in range(n):
        conv = f"conv-child-{uuid.uuid4().hex[:8]}"
        topic = f"signal:{conv}:done"
        conv_ids.append(conv)
        topics.append(topic)
        meta = _build_metadata(topic, responsibilities, allowed_paths)
        _dispatch_subtask(send_message_fn, resolved_target, task, conv, meta)

    return {"ok": True, "children": conv_ids, "topics": topics}


__all__ = ["orchestrate_split"]
