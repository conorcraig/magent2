from __future__ import annotations

import os
import signal
import time
import uuid
from collections.abc import Iterable
from typing import Any

from magent2.bus.redis_adapter import RedisBus
from magent2.models.envelope import BaseStreamEvent, MessageEnvelope, OutputEvent, TokenEvent
from magent2.observability import get_json_logger
from magent2.runner.config import load_config
from magent2.runner.openai_agents_runner import OpenAIAgentsRunner
from magent2.runner.openai_responses_runner import OpenAIResponsesRunner
from magent2.tools.registry import discover_tools
from magent2.worker.worker import Runner, Worker


class EchoRunner(Runner):
    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        yield TokenEvent(conversation_id=envelope.conversation_id, text="echo", index=0)
        yield OutputEvent(conversation_id=envelope.conversation_id, text=f"{envelope.content}")


def build_runner_from_env() -> Runner:
    cfg = load_config()
    if cfg.api_key:
        from agents import Agent  # defer import to avoid issues in Echo mode

        tools = discover_tools(cfg.agent_name, cfg.tools)
        _tools_any: Any = tools  # satisfy type checker without SDK-specific types
        agent = Agent(
            name=cfg.agent_name, instructions=cfg.instructions, model=cfg.model, tools=_tools_any
        )
        # Structured tools discovery for observability
        tool_names_list = [getattr(t, "__name__", "tool") for t in tools]
        get_json_logger("magent2").info(
            "runner selected",
            extra={
                "event": "runner_selected",
                "service": "worker",
                "agent": cfg.agent_name,
                "model": cfg.model,
                "kv": {"tools": tool_names_list, "tool_count": len(tool_names_list)},
            },
        )
        return OpenAIAgentsRunner(agent)
    # Fallback when API key present but Agents path not selected
    if os.getenv("OPENAI_API_KEY"):
        model = os.getenv("AGENT_MODEL", "gpt-4o-mini")
        get_json_logger("magent2").info(
            "runner selected",
            extra={
                "event": "runner_selected",
                "runner": "OpenAIResponses",
                "agent": cfg.agent_name,
                "model": model,
            },
        )
        return OpenAIResponsesRunner(model)
    get_json_logger("magent2").info(
        "runner selected",
        extra={
            "event": "runner_selected",
            "service": "worker",
            "runner": "Echo",
            "agent": cfg.agent_name,
        },
    )
    return EchoRunner()


_should_exit = False


def _handle_exit(signum: int, frame: Any) -> None:
    global _should_exit
    _should_exit = True


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
    # Install graceful shutdown handlers (SIGTERM/SIGINT)
    signal.signal(signal.SIGTERM, _handle_exit)
    signal.signal(signal.SIGINT, _handle_exit)

    # Simple loop: poll until exit is requested
    # Keep exit latency bounded by Redis block (1s) and sleep intervals
    sleep_seconds = 0.05
    max_sleep_seconds = 0.2
    while not _should_exit:
        processed = worker.process_available(limit=100)
        if processed == 0:
            time.sleep(sleep_seconds)
            # Exponential backoff with cap
            sleep_seconds = min(max_sleep_seconds, sleep_seconds * 2)
        else:
            # Reset backoff after successful processing
            sleep_seconds = 0.05


if __name__ == "__main__":
    main()
