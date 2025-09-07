from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from magent2.bus.redis_adapter import RedisBus
from magent2.models.envelope import BaseStreamEvent, MessageEnvelope, OutputEvent, TokenEvent
from magent2.runner.config import load_config
from magent2.runner.openai_agents_runner import OpenAIAgentsRunner
from magent2.worker.worker import Runner, Worker


class EchoRunner(Runner):
    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        yield TokenEvent(conversation_id=envelope.conversation_id, text="echo", index=0)
        yield OutputEvent(conversation_id=envelope.conversation_id, text=f"{envelope.content}")


def build_runner_from_env() -> Runner:
    cfg = load_config()
    if cfg.api_key:
        from agents import Agent  # defer import to avoid issues in Echo mode

        agent = Agent(name=cfg.agent_name, instructions=cfg.instructions, model=cfg.model)
        return OpenAIAgentsRunner(agent)
    return EchoRunner()


def main() -> None:
    cfg = load_config()
    bus = RedisBus(redis_url=os.getenv("REDIS_URL"))
    runner: Runner = build_runner_from_env()
    worker = Worker(agent_name=cfg.agent_name, bus=bus, runner=runner)
    # Simple loop: poll until interrupted
    try:
        while True:
            worker.process_available(limit=100)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
