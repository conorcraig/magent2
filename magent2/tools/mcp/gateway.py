from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from magent2.observability import get_json_logger

from .client import MCPClient, spawn_stdio_server
from .config import MCPServerConfig


@dataclass(slots=True)
class ToolInfo:
    name: str
    description: str | None
    input_schema: dict[str, Any]


class MCPToolGateway:
    """Manage multiple MCP stdio servers and expose a unified tool surface."""

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = configs
        # Map tool name -> owning client index
        self._tool_owner: dict[str, int] = {}
        self._clients: list[MCPClient] = []
        self._contexts: list[object] = []
        self._client_started: bool = False
        self._tool_cache: list[ToolInfo] | None = None

    # Lifecycle
    def start(self) -> None:
        if self._client_started:
            return
        for cfg in self._configs:
            cmd = [cfg.command, *cfg.args]

            # Strict allowlist: do NOT inherit the parent process environment.
            # Start from minimal, safe defaults only, then apply explicit overrides from config.
            env: dict[str, str] = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
                "LC_ALL": "C",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            }
            if cfg.env:
                env.update(cfg.env)
            ctx = spawn_stdio_server(cmd, cwd=cfg.cwd, env=env)
            # Enter the context to get a live client; store the client and its exit stack
            client = ctx.__enter__()
            try:
                timeout = cfg.init_timeout_seconds if cfg.init_timeout_seconds is not None else 5.0
                client.initialize(timeout=timeout)
            except Exception:
                # Ensure proper cleanup on failure to initialize
                try:
                    ctx.__exit__(None, None, None)
                finally:
                    raise
            self._clients.append(client)
            # Retain the context manager to prevent premature finalization
            self._contexts.append(ctx)
        # Build tool index now so list/call are efficient
        self._index_tools()
        self._client_started = True
        self._tool_cache = None

    def _index_tools(self) -> None:
        self._tool_owner.clear()
        for index, (config, client) in enumerate(zip(self._configs, self._clients)):
            for name in self._iter_server_tool_names(client):
                if self._is_exposed_by_policy(config, name) and name not in self._tool_owner:
                    self._tool_owner[name] = index
        self._tool_cache = None

    def _iter_server_tool_names(self, client: MCPClient) -> list[str]:
        try:
            tools = client.list_tools()
        except Exception:
            return []
        names: list[str] = []
        for tool in tools:
            name = tool.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def _is_exposed_by_policy(self, config: MCPServerConfig, name: str) -> bool:
        # Default-deny unless allowlist present; expose allow - block.
        if config.allow is None:
            return False
        if name not in config.allow:
            return False
        if config.block and name in config.block:
            return False
        return True

    def list_tools(self) -> list[ToolInfo]:
        if self._tool_cache is not None:
            return list(self._tool_cache)
        result: list[ToolInfo] = []
        for name, idx in self._tool_owner.items():
            client = self._clients[idx]
            # Find the tool metadata from the server
            try:
                tools = client.list_tools(timeout=3.0)
            except Exception:
                # If listing fails now, skip this tool
                continue
            for tool in tools:
                if tool.get("name") != name:
                    continue
                description = tool.get("description")
                if not isinstance(description, str):
                    description = None
                input_schema = tool.get("inputSchema") or {}
                if not isinstance(input_schema, dict):
                    input_schema = {}
                result.append(
                    ToolInfo(
                        name=name,
                        description=description,
                        input_schema=input_schema,
                    )
                )
                break
        self._tool_cache = list(result)
        return result

    def call(
        self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        if name not in self._tool_owner:
            raise KeyError(f"Unknown tool: {name}")
        idx = self._tool_owner[name]
        client = self._clients[idx]
        if timeout is None:
            return client.call_tool(name, arguments, timeout=10.0)
        return client.call_tool(name, arguments, timeout=timeout)

    def close(self) -> None:
        # Close via stored context managers to ensure cleanup in correct order
        logger = get_json_logger("magent2.tools.mcp") if "get_json_logger" in globals() else None
        if logger is not None:
            logger.debug("mcp gateway close", extra={"event": "mcp_close"})
        for ctx in reversed(self._contexts):
            try:
                # mypy: dynamic protocol (__exit__ exists on context managers)
                ctx.__exit__(None, None, None)  # type: ignore[attr-defined]
            except Exception:
                if logger is not None:
                    logger.debug("mcp gateway close error", extra={"event": "mcp_close_error"})
                pass
        self._clients.clear()
        self._contexts.clear()
        self._tool_owner.clear()
        self._client_started = False
        self._tool_cache = None


__all__ = ["ToolInfo", "MCPToolGateway"]
