from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _write_echo_server_script(tmp_path: Path) -> Path:
    """Create a minimal MCP stdio JSON-RPC server with one tool: echo(text)."""
    script = tmp_path / "echo_mcp_server.py"
    script.write_text(
        """
import io, json, sys

def read_frame(inp: io.BufferedReader) -> dict:
    # Read Content-Length header and body
    line = inp.readline()
    if not line:
        return {}
    line = line.decode().strip()
    if not line.lower().startswith('content-length:'):
        raise RuntimeError('Missing Content-Length')
    length = int(line.split(':', 1)[1].strip())
    # Read empty line
    blank = inp.readline()
    if not blank:
        raise RuntimeError('Missing blank line')
    body = inp.read(length)
    return json.loads(body.decode())

def write_frame(out: io.BufferedWriter, payload: dict) -> None:
    data = json.dumps(payload).encode()
    header = 'Content-Length: ' + str(len(data)) + '\\r\\n'
    out.write(header.encode())
    out.write(b'\\r\\n')
    out.write(data)
    out.flush()

def main() -> int:
    inp = sys.stdin.buffer
    out = sys.stdout.buffer
    while True:
        try:
            msg = read_frame(inp)
        except Exception as e:
            # Exit on framing errors
            return 1
        if not msg:
            return 0
        mid = msg.get('id')
        method = msg.get('method')
        params = msg.get('params') or {}
        if method == 'initialize':
            result = {
                'protocolVersion': '1.0',
                'capabilities': {'tools': True},
            }
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': result})
        elif method == 'tools/list':
            tools = [
                {
                    'name': 'echo',
                    'description': 'Echo back text',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {'text': {'type': 'string'}},
                        'required': ['text'],
                    },
                }
            ]
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': tools}})
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments') or {}
            if name == 'echo':
                text = arguments.get('text', '')
                write_frame(
                    out,
                    {
                        'jsonrpc': '2.0',
                        'id': mid,
                        'result': {'content': text},
                    },
                )
            else:
                write_frame(
                    out,
                    {
                        'jsonrpc': '2.0',
                        'id': mid,
                        'error': {
                            'code': -32601,
                            'message': 'Unknown tool',
                        },
                    },
                )
        elif method == 'shutdown':
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'ok': True}})
            return 0
        else:
            write_frame(
                out,
                {
                    'jsonrpc': '2.0',
                    'id': mid,
                    'error': {
                        'code': -32601,
                        'message': 'Method not found',
                    },
                },
            )

if __name__ == '__main__':
    raise SystemExit(main())
        """,
        encoding="utf-8",
    )
    return script
def _write_secret_server_script(tmp_path: Path) -> Path:
    """Create an MCP server with one tool: secret(code)."""
    script = tmp_path / "secret_mcp_server.py"
    script.write_text(
        """
import io, json, sys

def read_frame(inp: io.BufferedReader) -> dict:
    line = inp.readline()
    if not line:
        return {}
    line = line.decode().strip()
    if not line.lower().startswith('content-length:'):
        raise RuntimeError('Missing Content-Length')
    length = int(line.split(':', 1)[1].strip())
    blank = inp.readline()
    if not blank:
        raise RuntimeError('Missing blank line')
    body = inp.read(length)
    return json.loads(body.decode())

def write_frame(out: io.BufferedWriter, payload: dict) -> None:
    data = json.dumps(payload).encode()
    header = 'Content-Length: ' + str(len(data)) + '\r\n'
    out.write(header.encode())
    out.write(b'\r\n')
    out.write(data)
    out.flush()

def main() -> int:
    inp = sys.stdin.buffer
    out = sys.stdout.buffer
    while True:
        try:
            msg = read_frame(inp)
        except Exception:
            return 1
        if not msg:
            return 0
        mid = msg.get('id')
        method = msg.get('method')
        params = msg.get('params') or {}
        if method == 'initialize':
            result = {
                'protocolVersion': '1.0',
                'capabilities': {'tools': True},
            }
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': result})
        elif method == 'tools/list':
            tools = [
                {
                    'name': 'secret',
                    'description': 'Returns code',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {'code': {'type': 'string'}},
                        'required': ['code'],
                    },
                },
            ]
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': tools}})
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments') or {}
            if name == 'secret':
                code = arguments.get('code', '')
                write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'secret': code}})
            else:
                write_frame(
                    out,
                    {
                        'jsonrpc': '2.0',
                        'id': mid,
                        'error': {
                            'code': -32601,
                            'message': 'Unknown tool',
                        },
                    },
                )
        elif method == 'shutdown':
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'ok': True}})
            return 0
        else:
            write_frame(
                out,
                {
                    'jsonrpc': '2.0',
                    'id': mid,
                    'error': {
                        'code': -32601,
                        'message': 'Method not found',
                    },
                },
            )

if __name__ == '__main__':
    raise SystemExit(main())
        """,
        encoding="utf-8",
    )
    return script



def test_mcp_stdio_echo(tmp_path: Path) -> None:
    from magent2.tools.mcp.client import MCPClient, spawn_stdio_server

    server_script = _write_echo_server_script(tmp_path)
    cmd = [sys.executable, "-u", str(server_script)]

    client: MCPClient
    with spawn_stdio_server(cmd) as client:
        init = client.initialize()
        assert init["protocolVersion"] == "1.0"

        tools = client.list_tools()
        names = [t["name"] for t in tools]
        assert "echo" in names

        result = client.call_tool("echo", {"text": "hello"})
        assert result["content"] == "hello"


def test_gateway_with_echo_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_script = _write_echo_server_script(tmp_path)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_CMD", sys.executable)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ARGS", f"-u,{server_script}")
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ALLOW", "echo")

    from magent2.tools.mcp.config import load_agent_mcp_configs
    from magent2.tools.mcp.registry import load_for_agent

    cfgs = load_agent_mcp_configs("DevAgent")
    assert len(cfgs) == 1
    assert cfgs[0].allow is not None and "echo" in cfgs[0].allow
    gateway = load_for_agent("DevAgent")
    assert gateway is not None
    try:
        # Sanity: list_tools should expose echo tool
        tools = gateway.list_tools()
        names = {t.name for t in tools}
        assert names == {"echo"}
        result = gateway.call("echo", {"text": "ok"})
        assert result["content"] == "ok"
    finally:
        gateway.close()

def test_gateway_cleanup_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_script = _write_echo_server_script(tmp_path)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_CMD", sys.executable)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ARGS", f"-u,{server_script}")
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ALLOW", "echo")

    from magent2.tools.mcp.registry import load_for_agent

    gateway = load_for_agent("DevAgent")
    assert gateway is not None
    gateway.close()
    # Second close should be a no-op without raising
    gateway.close()

def test_gateway_lists_and_calls_filtered_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: local echo server; allowlist only echo
    echo_script = _write_echo_server_script(tmp_path)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_CMD", sys.executable)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ARGS", f"-u,{echo_script}")
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ALLOW", "echo")

    from magent2.tools.mcp.registry import load_for_agent

    gateway = load_for_agent("DevAgent")
    assert gateway is not None
    try:
        tools = gateway.list_tools()
        names = {t.name for t in tools}
        assert names == {"echo"}
        # Call list again to exercise caching path
        tools2 = gateway.list_tools()
        names2 = {t.name for t in tools2}
        assert names2 == names
        result = gateway.call("echo", {"text": "hi"})
        assert result["content"] == "hi"
        # Ensure blocked/absent tool is not callable
        try:
            gateway.call("secret", {"code": "x"})
            assert False, "secret should not be exposed"
        except KeyError:
            pass
    finally:
        gateway.close()
