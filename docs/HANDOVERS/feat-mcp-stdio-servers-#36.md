# Handover — MCP stdio servers for agents (Issue #36)

- Branch name: `feat/mcp-stdio-servers-#36`
- Scope: Connect per-agent configured MCP stdio servers and expose a safe subset of discovered tools to the agent runtime. Include graceful timeouts/cleanup and tests, skipping external demo server tests if unavailable.

## Current state

- MCP client exists: `magent2/tools/mcp/client.py` provides `MCPClient` and `spawn_stdio_server()` with JSON-RPC framing, request/reply, timeout handling, and cleanup.
- Tests exist for the client framing over stdio: `tests/test_mcp_stdio.py` (creates a temp echo server with one `echo(text)` tool).
- No wiring yet to expose MCP tools to any Agent/Runner. The worker currently uses an `EchoRunner` placeholder (`magent2/worker/__main__.py`).
- Reference docs: `docs/refs/mcp.md` summarizes stdio framing and the minimal flow; `docs/refs/openai-agents-sdk.md` for future runner wiring.

## Requirements (Issue #36)

- Add per-agent MCP server config (command/args/env); start/connect via stdio client.
- Discover tools and expose a safe subset to the agent.
- Include graceful timeouts/cleanup; skip tests if the demo MCP server is unavailable.
- Validate with `just check` (ruff, mypy, tests, markdownlint, etc.).

## Approach & design

Add an SDK-agnostic adapter layer under `magent2/tools/mcp/` reusing `MCPClient`. Frozen contracts (envelope + Bus) remain unchanged.

### 1) Per-agent configuration

Introduce a configuration loader that resolves MCP servers for a given agent from environment variables. Indexed per-server entries (N = 0,1,...):

- `AGENT_<AgentName>_MCP_<N>_CMD` (e.g., `npx`)
- `AGENT_<AgentName>_MCP_<N>_ARGS` (comma-separated args; empty allowed)
- `AGENT_<AgentName>_MCP_<N>_CWD` (optional working directory)
- `AGENT_<AgentName>_MCP_<N>_ENV_JSON` (JSON object string for a sanitized env; default `{}`)
- `AGENT_<AgentName>_MCP_<N>_ALLOW` (comma-separated allowlist of tool names; ONLY expose these if set)
- `AGENT_<AgentName>_MCP_<N>_BLOCK` (comma-separated blocklist of tool names)

Policy:

- Default-deny unless allowlist is present (recommended). If both allow and block set, exposure = allow − block.
- Only pass the explicit `env` provided; do not inherit parent environment by default.

Implementation: `magent2/tools/mcp/config.py`

```python
# Sketch
from dataclasses import dataclass

@dataclass(slots=True)
class MCPServerConfig:
    command: str
    args: list[str]
    cwd: str | None
    env: dict[str, str]
    allow: set[str] | None
    block: set[str] | None

def load_agent_mcp_configs(agent_name: str) -> list[MCPServerConfig]:
    # Parse indexed env vars; split comma lists; parse ENV_JSON; return list
    ...
```

### 2) Gateway for discovery and invocation

Manage multiple configured servers, unify tool listing, and dispatch calls.

Implementation: `magent2/tools/mcp/gateway.py`

- Pydantic model `ToolInfo`:
  - `name: str`
  - `description: str | None`
  - `input_schema: dict[str, object]`

- `class MCPToolGateway`:
  - `start()`: spawn each server with `spawn_stdio_server(cmd, cwd=..., env=...)`, create `MCPClient`, `initialize()`.
  - `list_tools() -> list[ToolInfo]`: union of tools across servers; apply allow/block filtering per server; on name conflicts, first seen wins.
  - `call(name: str, arguments: dict[str, object] | None, timeout: float | None) -> dict[str, object]`: dispatch to the owner `MCPClient` via `tools/call`.
  - `close()`: idempotent; close all clients (sends `shutdown()` best-effort then terminates if needed).

Timeouts: rely on `MCPClient` defaults; accept per-call override propagated to `call_tool()`.

### 3) Registry entry point

Implementation: `magent2/tools/mcp/registry.py`

- `def load_for_agent(agent_name: str) -> MCPToolGateway | None`:
  - Load configs; return `None` if none.
  - Build gateway, `start()`, return it.

### 4) Exports

Update `magent2/tools/mcp/__init__.py` to export:

- `MCPServerConfig`, `load_agent_mcp_configs`
- `ToolInfo`, `MCPToolGateway`
- `load_for_agent`

## Safety

- Sanitized env; no implicit inheritance.
- Default-deny exposure unless explicitly allowed.
- Robust cleanup via `MCPClient.close()`; tolerate repeated calls.

## Testing plan (TDD)

Augment `tests/test_mcp_stdio.py` with gateway tests:

1) `test_gateway_lists_and_calls_filtered_tools`

- Temp server advertises `echo` and `secret` tools.
- Allowlist only `echo`; assert `list_tools()` exposes only `echo` and `call('echo', {text:'hi'})` returns `hi`.

2) `test_gateway_cleanup_idempotent`

- Create gateway, start, `close()`, `close()` again; no exception.

Optional demo server test (graceful skip):

Create `tests/test_mcp_demo_optional.py`:

- Probe availability (e.g., check `shutil.which("npx")`, then attempt to spawn `npx -y @modelcontextprotocol/server-memory --stdio`); on failure/timeout → `pytest.skip("demo MCP server unavailable")`.
- If available: `initialize`, `tools/list` smoke, then `close`.

Probe example:

```python
import shutil, subprocess, pytest

cmd = ["npx", "-y", "@modelcontextprotocol/server-memory", "--stdio"]
if shutil.which("npx") is None:
    pytest.skip("npx not available")
try:
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.terminate()
except Exception:
    pytest.skip("demo MCP server unavailable")
```

## Example env configuration

Agent `DevAgent`:

- `AGENT_DevAgent_MCP_0_CMD=npx`
- `AGENT_DevAgent_MCP_0_ARGS=-y,@modelcontextprotocol/server-memory,--stdio`
- `AGENT_DevAgent_MCP_0_ALLOW=echo,search`  (adjust to real tool names)

Filesystem variant:

- `AGENT_DevAgent_MCP_1_CMD=npx`
- `AGENT_DevAgent_MCP_1_ARGS=-y,@modelcontextprotocol/server-filesystem,--stdio,/path/to/dir`
- `AGENT_DevAgent_MCP_1_ALLOW=list,read`

## Internal references (offline)

- MCP stdio spec summary and example: `docs/refs/mcp.md`
- Local client: `magent2/tools/mcp/client.py`
- Existing client test: `tests/test_mcp_stdio.py`
- Quality gate: `just check`

## External commands (captured for offline use)

- Start demo memory server (stdio): `npx -y @modelcontextprotocol/server-memory --stdio`
- Start demo filesystem server (stdio): `npx -y @modelcontextprotocol/server-filesystem --stdio /path/to/dir`
- JSON-RPC methods used: `initialize`, `tools/list`, `tools/call`, `shutdown`

## Future runner alignment

Keep adapter SDK-agnostic. A future Agents SDK runner can map `ToolInfo` to the SDK’s function schema and dispatch calls through `MCPToolGateway.call()`.

## Risks & mitigations

- External demo servers missing → optional test skips.
- Process leaks → idempotent `close()`; best-effort `shutdown()`.
- Tool safety → default-deny; explicit allowlist required.

## Deliverables checklist

- [ ] `magent2/tools/mcp/config.py` with `MCPServerConfig` and `load_agent_mcp_configs()`
- [ ] `magent2/tools/mcp/gateway.py` with `ToolInfo` and `MCPToolGateway`
- [ ] `magent2/tools/mcp/registry.py` with `load_for_agent()`
- [ ] `magent2/tools/mcp/__init__.py` exports
- [ ] Gateway tests added to `tests/test_mcp_stdio.py`
- [ ] Optional demo test with graceful skip
- [ ] `just check` passes
