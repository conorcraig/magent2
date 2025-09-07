from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RunnerConfig:
    api_key: str | None
    agent_name: str
    model: str
    instructions: str
    tools: list[str]


def _read_instructions() -> str:
    path = os.getenv("AGENT_INSTRUCTIONS_FILE")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            # Fallback to env if file missing/unreadable
            pass
    return os.getenv("AGENT_INSTRUCTIONS", "You are a helpful assistant.")


def _read_tools() -> list[str]:
    raw = os.getenv("AGENT_TOOLS", "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def load_config(env: dict[str, str] | None = None) -> RunnerConfig:
    e: dict[str, Any] = dict(os.environ)
    if env:
        e.update(env)
    return RunnerConfig(
        api_key=e.get("OPENAI_API_KEY"),
        agent_name=e.get("AGENT_NAME", "DevAgent"),
        model=e.get("AGENT_MODEL", "gpt-4o-mini"),
        instructions=_read_instructions(),
        tools=_read_tools(),
    )


__all__ = ["RunnerConfig", "load_config"]

