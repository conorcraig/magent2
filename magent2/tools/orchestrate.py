from __future__ import annotations

import importlib
import os
import uuid
from typing import Any

from magent2.tools.chat.function_tools import send_message
from magent2.tools.signals.wrappers import signal_wait_all


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
    """Split a task across N child agents and optionally wait for completion.

    - Uses structured metadata for orchestration hints
    - Parameterizes the target agent (explicit > env > default)
    - Allows configurable wait timeout via ``timeout_ms``
    """
    n = max(0, int(num_children))
    conv_ids: list[str] = []
    topics: list[str] = []

    # Resolve target agent: explicit > ORCHESTRATE_TARGET_AGENT > AGENT_NAME > DevAgent
    resolved_target = (
        (target_agent or "").strip()
        or os.getenv("ORCHESTRATE_TARGET_AGENT", "").strip()
        or os.getenv("AGENT_NAME", "").strip()
        or "DevAgent"
    )

    for _ in range(n):
        conv = f"conv-child-{uuid.uuid4().hex[:8]}"
        topic = f"signal:{conv}:done"
        conv_ids.append(conv)
        topics.append(topic)

        metadata = {
            "orchestrate": {
                "responsibilities": list(responsibilities or []),
                "allowed_paths": list(allowed_paths or []),
                "done_topic": topic,
            }
        }

        send_message(
            f"agent:{resolved_target}",
            f"Subtask for: {task}",
            conversation_id=conv,
            metadata=metadata,
        )

    result: dict[str, Any] = {"ok": True, "children": conv_ids, "topics": topics}
    if wait and topics:
        res = signal_wait_all(topics, last_ids=None, timeout_ms=int(timeout_ms))
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
