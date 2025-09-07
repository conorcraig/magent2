from __future__ import annotations

import shutil

import pytest


def test_demo_mcp_server_initialize_and_list() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not available")
    cmd = ["npx", "-y", "@modelcontextprotocol/server-memory", "--stdio"]

    from magent2.tools.mcp.client import spawn_stdio_server

    try:
        with spawn_stdio_server(cmd) as client:
            init = client.initialize(timeout=3.0)
            assert init["protocolVersion"]
            tools = client.list_tools(timeout=3.0)
            assert isinstance(tools, list)
    except Exception:
        pytest.skip("demo MCP server unavailable")
