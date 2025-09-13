from __future__ import annotations

from typing import Any

from agents import function_tool

from magent2.observability import get_json_logger, get_metrics, get_run_context

from .impl import send_signal as _send_signal_impl
from .impl import wait_for_all as _wait_for_all_impl
from .impl import wait_for_any as _wait_for_any_impl
from .impl import wait_for_signal as _wait_for_signal_impl


@function_tool
def signal_send(topic: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    logger = get_json_logger("magent2.tools")
    metrics = get_metrics()
    ctx = get_run_context() or {}
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool": "signals.send",
            "metadata": {"topic": topic},
        },
    )
    metrics.increment(
        "tool_calls",
        {
            "tool": "signals",
            "conversation_id": str(ctx.get("conversation_id", "")),
            "run_id": str(ctx.get("run_id", "")),
        },
    )
    try:
        result = _send_signal_impl(topic, payload or {})
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "signals.send",
                "metadata": {"topic": topic},
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "signals.send",
                "metadata": {"error": str(exc)[:200]},
            },
        )
        metrics.increment(
            "tool_errors",
            {
                "tool": "signals",
                "conversation_id": str(ctx.get("conversation_id", "")),
                "run_id": str(ctx.get("run_id", "")),
            },
        )
        raise


@function_tool
def signal_wait(topic: str, last_id: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
    logger = get_json_logger("magent2.tools")
    metrics = get_metrics()
    ctx = get_run_context() or {}
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool": "signals.wait",
            "metadata": {"topic": topic, "timeout_ms": timeout_ms},
        },
    )
    metrics.increment(
        "tool_calls",
        {
            "tool": "signals",
            "conversation_id": str(ctx.get("conversation_id", "")),
            "run_id": str(ctx.get("run_id", "")),
        },
    )
    try:
        result = _wait_for_signal_impl(topic, last_id=last_id, timeout_ms=timeout_ms)
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "signals.wait",
                "metadata": {"topic": topic},
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "signals.wait",
                "metadata": {"error": str(exc)[:200]},
            },
        )
        metrics.increment(
            "tool_errors",
            {
                "tool": "signals",
                "conversation_id": str(ctx.get("conversation_id", "")),
                "run_id": str(ctx.get("run_id", "")),
            },
        )
        raise


@function_tool
def signal_wait_any(
    topics: list[str], last_ids: dict[str, str] | None = None, timeout_ms: int = 30000
) -> dict[str, Any]:
    return _wait_for_any_impl(topics, last_ids=last_ids, timeout_ms=timeout_ms)


@function_tool
def signal_wait_all(
    topics: list[str], last_ids: dict[str, str] | None = None, timeout_ms: int = 30000
) -> dict[str, Any]:
    return _wait_for_all_impl(topics, last_ids=last_ids, timeout_ms=timeout_ms)


__all__ = ["signal_send", "signal_wait", "signal_wait_any", "signal_wait_all"]
