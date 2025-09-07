# Model Context Protocol (MCP) – stdio JSON‑RPC

- Framing: Each message is `Content-Length: <n>\r\n`, blank line, then `n` bytes of JSON (UTF‑8).
- Protocol: JSON‑RPC 2.0 – request has `id`, `method`, `params`. Response has `id`, `result` or `error`.
- Typical flow: `initialize` → `tools/list` → `tools/call {name, arguments}` → `shutdown`.
- Robustness: enforce read timeouts, validate Content‑Length, handle EOF; serialize requests, match responses by numeric `id`.

## Example (using local MCP client)

```python
from magent2.tools.mcp.client import spawn_stdio_server

# Replace with a real server, e.g.: ["npx","-y","@modelcontextprotocol/server-memory"]
cmd = ["your-mcp-server", "--stdio"]
with spawn_stdio_server(cmd) as client:
    client.initialize()
    tools = client.list_tools()
    print(tools)
    # res = client.call_tool("tool_name", {"arg": 1})
```

## References

- modelcontextprotocol.io docs/spec; MCP GitHub org
