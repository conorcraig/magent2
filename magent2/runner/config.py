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
    max_turns: int


def _read_instructions() -> str:
    path = os.getenv("AGENT_INSTRUCTIONS_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
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
    # Parse max turns from env with sensible default
    max_turns_raw = (e.get("AGENT_MAX_TURNS") or "").strip()
    try:
        max_turns_val = int(max_turns_raw) if max_turns_raw else 10
    except Exception:
        max_turns_val = 10
    max_turns = max(1, max_turns_val)
    return RunnerConfig(
        api_key=e.get("OPENAI_API_KEY"),
        agent_name=e.get("AGENT_NAME", "DevAgent"),
        model=e.get("AGENT_MODEL", "gpt-5"),
        instructions=_read_instructions(),
        tools=_read_tools(),
        max_turns=max_turns,
    )


__all__ = ["RunnerConfig", "load_config"]
