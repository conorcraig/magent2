from __future__ import annotations

import sys
from pathlib import Path
import os


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


def _write_multi_tool_server_script(tmp_path: Path) -> Path:
    """Server that exposes echo and secret tools for gateway filtering tests."""
    script = tmp_path / "multi_tool_mcp_server.py"
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
                    'name': 'echo',
                    'description': 'Echo back text',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {'text': {'type': 'string'}},
                        'required': ['text'],
                    },
                },
                {
                    'name': 'secret',
                    'description': 'Should not be exposed',
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
            if name == 'echo':
                text = arguments.get('text', '')
                write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'content': text}})
            elif name == 'secret':
                code = arguments.get('code', '')
                write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'secret': code}})
            else:
                write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'Unknown tool'}})
        elif method == 'shutdown':
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'result': {'ok': True}})
            return 0
        else:
            write_frame(out, {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'Method not found'}})

if __name__ == '__main__':
    raise SystemExit(main())
        """,
        encoding="utf-8",
    )
    return script


def test_gateway_lists_and_calls_filtered_tools(tmp_path: Path, monkeypatch) -> None:
    # Arrange: multi-tool local server
    server_script = _write_multi_tool_server_script(tmp_path)
    cmd = [sys.executable, "-u", str(server_script)]
    # Configure via env for agent "DevAgent"
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_CMD", sys.executable)
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ARGS", f"-u,{server_script}")
    monkeypatch.setenv("AGENT_DevAgent_MCP_0_ALLOW", "echo")

    from magent2.tools.mcp.registry import load_for_agent

    gateway = load_for_agent("DevAgent")
    assert gateway is not None
    try:
        tools = gateway.list_tools()
        names = {t.name for t in tools}
        assert names == {"echo"}
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


