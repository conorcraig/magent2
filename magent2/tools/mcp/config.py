from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MCPServerConfig:
    command: str
    args: list[str]
    cwd: str | None
    env: dict[str, str]
    allow: set[str] | None
    block: set[str] | None


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_csv_set(value: str | None) -> set[str] | None:
    items = _parse_csv(value)
    return set(items) if items else None


def _parse_env_json(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    try:
        data: Any = json.loads(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("Invalid ENV_JSON; must be a JSON object string") from exc
    if not isinstance(data, dict):
        raise ValueError("ENV_JSON must decode to an object")
    result: dict[str, str] = {}
    for k, v in data.items():
        # Force string values to avoid accidental binary/complex types
        result[str(k)] = str(v)
    return result


def load_agent_mcp_configs(agent_name: str) -> list[MCPServerConfig]:
    """Load per-agent MCP stdio server configurations from environment.

    Variables (N = 0,1,...):
    - AGENT_<AgentName>_MCP_<N>_CMD
    - AGENT_<AgentName>_MCP_<N>_ARGS  (comma-separated)
    - AGENT_<AgentName>_MCP_<N>_CWD
    - AGENT_<AgentName>_MCP_<N>_ENV_JSON  (JSON object)
    - AGENT_<AgentName>_MCP_<N>_ALLOW  (comma-separated)
    - AGENT_<AgentName>_MCP_<N>_BLOCK  (comma-separated)
    """
    prefix = f"AGENT_{agent_name}_MCP_"
    configs: list[MCPServerConfig] = []
    index = 0
    while True:
        base = f"{prefix}{index}_"
        cmd = os.getenv(f"{base}CMD")
        if not cmd:
            # Stop at first missing CMD; indexes are expected to be contiguous
            break
        args = _parse_csv(os.getenv(f"{base}ARGS"))
        cwd = os.getenv(f"{base}CWD") or None
        env = _parse_env_json(os.getenv(f"{base}ENV_JSON"))
        allow = _parse_csv_set(os.getenv(f"{base}ALLOW"))
        block = _parse_csv_set(os.getenv(f"{base}BLOCK"))

        configs.append(
            MCPServerConfig(
                command=cmd,
                args=args,
                cwd=cwd,
                env=env,
                allow=allow,
                block=block,
            )
        )
        index += 1

    return configs


__all__ = ["MCPServerConfig", "load_agent_mcp_configs"]
