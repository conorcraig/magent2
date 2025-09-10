from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterable
from typing import Any

from magent2.bus.redis_adapter import RedisBus
from magent2.models.envelope import BaseStreamEvent, MessageEnvelope, OutputEvent, TokenEvent
from magent2.observability import get_json_logger
from magent2.runner.config import load_config
from magent2.runner.demo_runner import DemoRunner
from magent2.runner.openai_agents_runner import OpenAIAgentsRunner
from magent2.worker.worker import Runner, Worker


class EchoRunner(Runner):
    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        yield TokenEvent(conversation_id=envelope.conversation_id, text="echo", index=0)
        yield OutputEvent(conversation_id=envelope.conversation_id, text=f"{envelope.content}")


def build_runner_from_env() -> Runner:
    cfg = load_config()
    # Allow explicit DEMO runner override for local demos
    mode = (os.getenv("AGENT_RUNNER_MODE") or "").strip().lower()
    if mode == "demo":
        get_json_logger("magent2").info(
            "runner selected",
            extra={
                "event": "runner_selected",
                "runner": "Demo",
                "agent": cfg.agent_name,
            },
        )
        return DemoRunner()
    if cfg.api_key:
        from agents import Agent  # defer import to avoid issues in Echo mode

        def _load_tools(names: list[str]) -> list[Any]:
            """Resolve configured tool names to decorated function tool objects.

            If no names are provided, include a safe default set of available tools.
            Unknown names are ignored.
            """
            available: dict[str, Any] = {}

            # Terminal tool (single-function)
            try:
                from magent2.tools.terminal.function_tools import terminal_run_tool

                available["terminal_run_tool"] = terminal_run_tool
            except Exception:
                pass

            # Chat tool (send message via bus)
            try:
                from magent2.tools.chat import chat_send

                available["chat_send"] = chat_send
            except Exception:
                pass

            # Signals tools (send/wait)
            try:
                from magent2.tools.signals.wrappers import signal_send, signal_wait

                available["signal_send"] = signal_send
                available["signal_wait"] = signal_wait
            except Exception:
                pass

            # Todo tools (CRUD)
            try:
                from magent2.tools.todo.tools import (
                    todo_create,
                    todo_delete,
                    todo_get,
                    todo_list,
                    todo_update,
                )

                available.update(
                    {
                        "todo_create": todo_create,
                        "todo_get": todo_get,
                        "todo_list": todo_list,
                        "todo_update": todo_update,
                        "todo_delete": todo_delete,
                    }
                )
            except Exception:
                pass

            # MCP tools (dynamic proxy -> function tools)
            try:
                from agents import function_tool  # needed to expose dynamic wrappers

                from magent2.tools.mcp.registry import load_for_agent

                gateway = load_for_agent(cfg.agent_name)
                if gateway is not None:
                    for info in gateway.list_tools():
                        tool_name = str(info.name)

                        def _make_proxy(name: str) -> Any:
                            @function_tool(name_override=name)
                            def _mcp_proxy(**kwargs: Any) -> dict[str, Any]:
                                # Dispatch to gateway with a default timeout
                                return gateway.call(name, arguments=kwargs, timeout=10.0)

                            return _mcp_proxy

                        # Only add if not shadowed by a built-in name
                        if tool_name not in available:
                            available[tool_name] = _make_proxy(tool_name)
            except Exception:
                # If MCP is misconfigured or decorator unavailable, skip silently
                pass

            resolved: list[Any] = []
            if names:
                for name in names:
                    tool = available.get(name)
                    if tool is not None:
                        resolved.append(tool)
                    else:
                        get_json_logger("magent2").warning(
                            "unknown tool name, skipping",
                            extra={"event": "config_warn", "tool": name},
                        )
            else:
                # Default to all detected tools for developer convenience
                resolved = list(available.values())
            return resolved

        tools = _load_tools(cfg.tools)
        _tools_any: Any = tools  # satisfy type checker without SDK-specific types
        agent = Agent(
            name=cfg.agent_name, instructions=cfg.instructions, model=cfg.model, tools=_tools_any
        )
        tool_names = ",".join([getattr(t, "__name__", "tool") for t in tools]) or "<none>"
        get_json_logger("magent2").info(
            "runner selected",
            extra={
                "event": "runner_selected",
                "runner": "OpenAI",
                "agent": cfg.agent_name,
                "model": cfg.model,
                "metadata": {"tools": tool_names},
            },
        )
        return OpenAIAgentsRunner(agent)
    get_json_logger("magent2").info(
        "runner selected",
        extra={"event": "runner_selected", "runner": "Echo", "agent": cfg.agent_name},
    )
    return EchoRunner()


def main() -> None:
    cfg = load_config()
    # Use consumer groups by default; allow disabling for simple local dev
    use_groups_raw = os.getenv("WORKER_USE_GROUPS", "1").strip().lower()
    use_groups = use_groups_raw not in {"0", "false", "no", "off"}
    if use_groups:
        # Configure a consumer group with a reasonable default and enable blocking reads
        bus = RedisBus(
            redis_url=os.getenv("REDIS_URL"),
            group_name="magent2",
            consumer_name=f"worker-{uuid.uuid4()}",
            block_ms=1000,
        )
    else:
        # Simple tail-based reads without consumer groups
        bus = RedisBus(redis_url=os.getenv("REDIS_URL"))
    runner: Runner = build_runner_from_env()
    worker = Worker(agent_name=cfg.agent_name, bus=bus, runner=runner)
    # Simple loop: poll until interrupted
    try:
        # Option A fallback: small exponential backoff when no messages are processed
        sleep_seconds = 0.05
        max_sleep_seconds = 0.2
        while True:
            processed = worker.process_available(limit=100)
            if processed == 0:
                time.sleep(sleep_seconds)
                # Exponential backoff with cap
                sleep_seconds = min(max_sleep_seconds, sleep_seconds * 2)
            else:
                # Reset backoff after successful processing
                sleep_seconds = 0.05
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
