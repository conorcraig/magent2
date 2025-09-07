from __future__ import annotations

import shutil
import subprocess
import sys

import pytest


def _is_demo_available() -> bool:
    if shutil.which("npx") is None:
        return False
    cmd = ["npx", "-y", "@modelcontextprotocol/server-memory", "--stdio"]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        return False
    finally:
        try:
            proc.terminate()  # type: ignore[unreachable]
        except Exception:
            pass
    return True


@pytest.mark.skipif(not _is_demo_available(), reason="demo MCP server unavailable")
def test_demo_mcp_server_initialize_and_list() -> None:
    cmd = ["npx", "-y", "@modelcontextprotocol/server-memory", "--stdio"]

    from magent2.tools.mcp.client import spawn_stdio_server

    with spawn_stdio_server(cmd) as client:
        init = client.initialize()
        assert init["protocolVersion"]
        tools = client.list_tools()
        assert isinstance(tools, list)

