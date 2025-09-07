from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        self._client_started: bool = False

    # Lifecycle
    def start(self) -> None:
        if self._client_started:
            return
        for cfg in self._configs:
            cmd = [cfg.command, *cfg.args]
            ctx = spawn_stdio_server(cmd, cwd=cfg.cwd, env=cfg.env)
            # Enter the context to get a live client; store the client and its exit stack
            client = ctx.__enter__()
            try:
                client.initialize()
            except Exception:
                # Ensure proper cleanup on failure to initialize
                try:
                    ctx.__exit__(None, None, None)
                finally:
                    raise
            self._clients.append(client)
        # Build tool index now so list/call are efficient
        self._index_tools()
        self._client_started = True

    def _index_tools(self) -> None:
        self._tool_owner.clear()
        for idx, (cfg, client) in enumerate(zip(self._configs, self._clients)):
            try:
                tools = client.list_tools()
            except Exception:
                # Skip misbehaving server; do not index any tools from it
                continue
            allow = cfg.allow
            block = cfg.block or set()
            for tool in tools:
                name = tool.get("name")
                if not isinstance(name, str) or not name:
                    continue
                # Policy: default-deny unless allowlist present.
                # If allowlist exists, expose only allow - block.
                if allow is None:
                    # No allowlist provided → expose nothing from this server
                    continue
                if name not in allow or name in block:
                    continue
                # First seen wins on conflicts
                if name not in self._tool_owner:
                    self._tool_owner[name] = idx

    def list_tools(self) -> list[ToolInfo]:
        result: list[ToolInfo] = []
        for name, idx in self._tool_owner.items():
            client = self._clients[idx]
            # Find the tool metadata from the server
            try:
                tools = client.list_tools()
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
                    ToolInfo(name=name, description=description, input_schema=input_schema)
                )
                break
        return result

    def call(self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        if name not in self._tool_owner:
            raise KeyError(f"Unknown tool: {name}")
        idx = self._tool_owner[name]
        client = self._clients[idx]
        if timeout is None:
            return client.call_tool(name, arguments)
        return client.call_tool(name, arguments, timeout=timeout)

    def close(self) -> None:
        # Idempotent close
        for client in self._clients:
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()
        self._tool_owner.clear()
        self._client_started = False


__all__ = ["ToolInfo", "MCPToolGateway"]

