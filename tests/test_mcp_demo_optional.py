from __future__ import annotations

import sys
from pathlib import Path

from tests.test_mcp_stdio import _write_echo_server_script as _write_demo_server_script


def test_demo_mcp_server_initialize_and_list(tmp_path: Path) -> None:
    from magent2.tools.mcp.client import spawn_stdio_server

    server_script = _write_demo_server_script(tmp_path)
    cmd = [sys.executable, "-u", str(server_script)]

    with spawn_stdio_server(cmd) as client:
        init = client.initialize(timeout=3.0)
        assert init["protocolVersion"]
        tools = client.list_tools(timeout=3.0)
        assert isinstance(tools, list)
