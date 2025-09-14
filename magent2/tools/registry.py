from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magent2.observability import get_json_logger


def _maybe_get_function_tool() -> Any | None:
    try:
        from agents import function_tool
    except Exception:
        return None
    return function_tool


def _safe_add(d: dict[str, Any], name: str, fn: Any) -> None:
    if name not in d and fn is not None:
        d[name] = fn


def _list_builtin_tools() -> dict[str, Any]:
    available: dict[str, Any] = {}

    # Terminal (function tool wrapper)
    try:
        from magent2.tools.terminal.function_tools import terminal_run_tool

        _safe_add(available, "terminal_run_tool", terminal_run_tool)
    except Exception:
        pass

    # Chat send
    try:
        from magent2.tools.chat import chat_send

        _safe_add(available, "chat_send", chat_send)
    except Exception:
        pass

    # Signals
    try:
        from magent2.tools.signals.wrappers import signal_send, signal_wait

        _safe_add(available, "signal_send", signal_send)
        _safe_add(available, "signal_wait", signal_wait)
    except Exception:
        pass

    # Todo CRUD tools
    try:
        from magent2.tools.todo.tools import (
            todo_create,
            todo_delete,
            todo_get,
            todo_list,
            todo_update,
        )

        _safe_add(available, "todo_create", todo_create)
        _safe_add(available, "todo_get", todo_get)
        _safe_add(available, "todo_list", todo_list)
        _safe_add(available, "todo_update", todo_update)
        _safe_add(available, "todo_delete", todo_delete)
    except Exception:
        pass

    return available


def _list_mcp_tools(agent_name: str) -> dict[str, Any]:
    """Return MCP tools proxied as function tools when possible.

    If the agents SDK decorator isn't available or gateway isn't configured,
    return an empty mapping.
    """
    decorator = _maybe_get_function_tool()
    if decorator is None:
        return {}

    try:
        from magent2.tools.mcp.registry import load_for_agent
    except Exception:
        return {}

    gateway = None
    try:
        gateway = load_for_agent(agent_name)
    except Exception:
        gateway = None
    if gateway is None:
        return {}

    available: dict[str, Any] = {}

    try:
        for info in gateway.list_tools():
            tool_name = str(getattr(info, "name", "") or "").strip()
            if not tool_name:
                continue

            def _make_proxy(name: str) -> Any:
                @decorator(name_override=name)
                def _mcp_proxy(**kwargs: Any) -> dict[str, Any]:
                    return gateway.call(name, arguments=kwargs, timeout=10.0)

                return _mcp_proxy

            if tool_name not in available:
                available[tool_name] = _make_proxy(tool_name)
    except Exception:
        # If listing fails, treat as no MCP tools
        return {}

    return available


def list_available_tools(agent_name: str) -> dict[str, Callable[..., Any]]:
    """Return a mapping of tool_name -> callable for all detectable tools.

    Includes built-ins and MCP tools (if configured and decorator available).
    """
    available: dict[str, Any] = {}
    available.update(_list_builtin_tools())
    try:
        available.update(_list_mcp_tools(agent_name))
    except Exception:
        pass
    return available


def _env_true(name: str, default: bool = False) -> bool:
    import os

    raw = (os.getenv(name) or ("1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def discover_tools(agent_name: str, requested: list[str] | None) -> list[Callable[..., Any]]:
    """Resolve the final tool list given an optional requested allowlist.

    Behavior:
    - If `requested` is non-empty, return only those names found; warn on unknowns.
    - If `requested` is empty/None:
      - If `AGENT_REQUIRE_EXPLICIT_TOOLS=1`, return empty (prod hardening).
      - Otherwise, return all detected tools (dev convenience).
    """
    logger = get_json_logger("magent2")
    available = list_available_tools(agent_name)

    if requested:
        resolved: list[Any] = []
        for name in requested:
            tool = available.get(name)
            if tool is not None:
                resolved.append(tool)
            else:
                try:
                    logger.warning(
                        "unknown tool name, skipping",
                        extra={"event": "config_warn", "tool": name},
                    )
                except Exception:
                    pass
        return resolved

    # No explicit list provided
    if _env_true("AGENT_REQUIRE_EXPLICIT_TOOLS", default=False):
        return []

    return list(available.values())


__all__ = ["list_available_tools", "discover_tools"]
