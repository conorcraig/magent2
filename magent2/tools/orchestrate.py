from __future__ import annotations

import importlib
import uuid
from typing import Any

import os

from magent2.tools.chat.function_tools import send_message
from magent2.tools.signals.wrappers import signal_wait_all


def orchestrate_split(
    task: str,
    num_children: int = 2,
    *,
    responsibilities: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """Split a task across N child agents and optionally wait for completion.

    - Spawns child conversations for the same agent (DevAgent) by publishing kickoff messages.
    - Encodes responsibilities/allowed_paths/done_topic hints inline in message content.
    - When wait=True, waits for all child "done" signals and returns the wait result.
    """
    n = max(0, int(num_children))
    conv_ids: list[str] = []
    topics: list[str] = []
    for _ in range(n):
        conv = f"conv-child-{uuid.uuid4().hex[:8]}"
        topic = f"signal:{conv}:done"
        conv_ids.append(conv)
        topics.append(topic)
        hint = (
            " [parent: responsibilities="
            + ",".join(list(responsibilities or []))
            + "; allowed_paths="
            + ",".join(list(allowed_paths or []))
            + "; done_topic="
            + topic
            + "]"
        )
        send_message("agent:DevAgent", f"Subtask for: {task}.{hint}", conversation_id=conv)
    result: dict[str, Any] = {"ok": True, "children": conv_ids, "topics": topics}
    if wait and topics:
        res = signal_wait_all(topics, last_ids=None, timeout_ms=30000)
        result["wait"] = res
        result["ok"] = bool(res.get("ok"))
    return result


def _maybe_get_function_tool() -> Any | None:
    try:
        module = importlib.import_module("agents")
    except Exception:  # noqa: BLE001
        return None
    return getattr(module, "function_tool", None)


_function_tool = _maybe_get_function_tool()

# Only register the function tool when explicitly enabled to avoid test-time schema issues
if _function_tool is not None and (os.getenv("ENABLE_ORCHESTRATE_TOOL") == "1"):

    @_function_tool
    def orchestrate_split_tool(
        task: str,
        num_children: int = 2,
    ) -> dict[str, Any]:  # pragma: no cover - thin wrapper
        return orchestrate_split(task, num_children=num_children, wait=False)

    __all__ = ["orchestrate_split", "orchestrate_split_tool"]
else:
    __all__ = ["orchestrate_split"]