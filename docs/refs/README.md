# Reference notes (concise) – links for deep dives only

This file captures the core information we rely on. Links are provided only as jump‑off points.

## Redis Streams (Bus semantics)
- Streams store ordered entries; each entry has a Redis entry id (e.g. 1712345678901-0) and arbitrary field map.
- Append: XADD stream key with fields. We store a canonical UUID in field id and the JSON payload in field payload.
- Tail reads without groups: XRANGE/XREVRANGE; to read after cursor, seek after a known entry id.
- Consumer groups: XGROUP creates a group at an id (often 0); XREADGROUP with > delivers only new entries to the group.
- Acknowledgement: XACK marks entries as processed for the group; unacked entries show in XPENDING.
- Delivery: At‑least‑once. Consumers must be idempotent and/or dedupe by canonical UUID.
- Cursors:
  - If you track Redis entry ids, you can fetch after that id efficiently.
  - If you track your own UUIDs in a field, you may need a scan to find the corresponding entry id, then continue from there.
- Topics we use:
  - Inbound chat: chat:{conversation_id} and chat:{agent_name}
  - Streamed events: stream:{conversation_id}
  - Control: control:{agent_name} (pause/resume etc.)

References: Redis Streams overview; XADD/XREADGROUP/XPENDING docs.

Example (redis-py – append and read):

```python
import json, uuid, redis

r = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
topic = "chat:example"

# Append with canonical UUID + JSON payload
bus_id = str(uuid.uuid4())
r.xadd(topic, {"id": bus_id, "payload": json.dumps({"content": "hello"})})

# Tail read last 10 entries (no group)
entries = r.xrevrange(topic, "+", "-", count=10) or []
entries.reverse()
for entry_id, fields in entries:
    payload = json.loads(fields.get("payload", "{}"))
    print(entry_id, fields.get("id"), payload)

# Consumer group read + ack
group, consumer = "g1", "c1"
try:
    r.xgroup_create(topic, group, id="0", mkstream=True)
except Exception as e:
    if "BUSYGROUP" not in str(e):
        raise
resp = r.xreadgroup(groupname=group, consumername=consumer, streams={topic: ">"}, count=10, block=0)
for _, items in (resp or []):
    for entry_id, fields in items:
        # process ...
        r.xack(topic, group, entry_id)
```

## Server‑Sent Events (SSE) (Gateway streaming)
- Protocol: HTTP response with Content-Type: text/event-stream. Events are lines prefixed with data: , separated by a blank line.
- Multiple data lines per event are allowed; clients concatenate them. We emit one JSON per event line.
- Keep‑alive: leave connection open; send periodic heartbeat comments if needed. Disable proxy buffering for real‑time delivery.
- Client: EventSource in browsers auto‑reconnects; you can use Last-Event-ID to resume if you emit it.
- FastAPI: use StreamingResponse(generator, media_type="text/event-stream") and ensure the generator yields strings ending with \n\n.

References: MDN SSE; FastAPI StreamingResponse; reverse proxy buffering notes.

Example (FastAPI SSE endpoint + JS client):

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio, json

app = FastAPI()

async def event_gen():
    for i in range(3):
        yield f"data: {json.dumps({'event':'token','i':i})}\n\n"
        await asyncio.sleep(0.05)

@app.get("/stream")
async def stream():
    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

```javascript
const es = new EventSource("/stream");
es.onmessage = (e) => console.log(JSON.parse(e.data));
```

## Model Context Protocol (MCP) (stdio JSON‑RPC)
- Framing: Each message is Content-Length: <n>\r\n, blank line, then n bytes of JSON (UTF‑8).
- Protocol: JSON‑RPC 2.0 – request has id, method, params. Response has id, result or error.
- Typical flow: initialize → tools/list → tools/call {name, arguments} → shutdown.
- Robustness: enforce read timeouts, validate Content‑Length, handle EOF; serialize requests, match responses by numeric id.

References: modelcontextprotocol.io docs/spec.

Example (using our MCP client):

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

## Observability (traces, logs, metrics)
- Correlation: include conversation_id and a per‑run run_id on all logs and events.
- Tracing: start a span for Worker run; create child spans for tool calls; propagate context through Runner/Tools.
- Logging: JSON logs with minimal stable keys (timestamp, level, message, run_id, conversation_id, agent, tool).
- Metrics: counters for runs started/completed/errored; tool calls; retries; DLQ size.
- Redaction: never log secrets or full command lines with sensitive args; sanitize outputs.

References: OpenTelemetry Python; Python logging cookbook.

Example (minimal JSON logging with correlation):

```python
import json, logging, sys

class JsonHandler(logging.StreamHandler):
    def emit(self, record):
        msg = {
            "level": record.levelname,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "conversation_id": getattr(record, "conversation_id", None),
        }
        sys.stdout.write(json.dumps(msg) + "\n")

logger = logging.getLogger("magent2")
logger.setLevel(logging.INFO)
logger.addHandler(JsonHandler())

logger.info("run_started", extra={"run_id": "r1", "conversation_id": "c1"})
```

## Safe subprocess (Terminal tool)
- Never use shell=True; build argv with shlex.split and pass to Popen.
- Environment: start from a minimal map; set safe PATH; inject only explicit allowlisted env vars.
- Policy: enforce command allowlist (by basename), wall‑clock timeout, output byte cap; non‑interactive (no stdin).
- Termination: on timeout, kill the entire process group (e.g., os.killpg) and drain pipes.
- Sandbox: optional working directory sandbox; canonicalize cwd/paths; deny path escapes if policy requires.

References: Python subprocess docs; OWASP Command Injection.

Example (safe subprocess with timeout + process‑group kill):

```python
import os, shlex, signal
from subprocess import Popen, DEVNULL, PIPE, TimeoutExpired

argv = shlex.split("echo hello")
env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
proc = Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True, start_new_session=True, env=env)
try:
    out, err = proc.communicate(timeout=2)
except TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    out, err = proc.communicate()
print(out)
```

## Docker + pytest‑docker (E2E)
- Compose healthcheck gates service readiness; tests should wait until responsive (e.g., HTTP /health) before proceeding.
- Avoid fixed host ports in tests; discover host port via docker_services.port_for(service, internal_port).
- Keep a single compose file as source of truth; parameterize ports with env vars for local pinning if needed.

References: pytest‑docker docs; Compose healthcheck docs.

Example (pytest‑docker wait + dynamic port):

```python
def is_responsive(url: str) -> bool: ...

def test_stack(docker_services):
    port = docker_services.port_for("gateway", 8000)
    docker_services.wait_until_responsive(
        timeout=60.0, pause=0.5,
        check=lambda: is_responsive(f"http://localhost:{port}/health"),
    )
```

## Quality gates (Ruff, mypy, secrets, complexity)
- Ruff: checks + formatter; keep config minimal and project‑wide.
- mypy: strict where feasible (no implicit Optional, disallow untyped defs); manage baselines for legacy code.
- detect‑secrets: maintain a baseline; run in pre‑commit and CI; never commit real secrets.
- Xenon/Radon: set complexity thresholds; fail CI when functions exceed target score; ratchet down over time.

References: Ruff, mypy, detect‑secrets, Xenon/Radon docs.

Examples:

```toml
[tool.mypy]
disallow_untyped_defs = true
no_implicit_optional = true
```

```bash
uv run ruff check && uv run mypy && uv run pytest -q
```

## uv + GitHub Actions (CI)
- Use uv to install/sync; cache uv downloads/venv to speed builds.
- Cancel in‑progress jobs per branch to reduce churn; emit JUnit/JSON reports as artifacts.
- Keep CI steps: ruff check/format (dry run), mypy, pytest (unit/e2e), secrets scan.

References: uv + GitHub Actions guide; GHA caching docs.

Example (GitHub Actions steps with uv):

```yaml
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/uv-action@v1
      - run: uv sync
      - run: uv run ruff check
      - run: uv run mypy
      - run: uv run pytest -q
```

## OpenAI platform (general)
- Rate limits: plan retry/backoff for model calls; consider token budgets.
- Function/tool calling: define strict JSON schemas; validate inputs server‑side; keep tools idempotent where possible.
- Streaming: prefer streamed responses for UX; map partials to TokenEvent; emit a final OutputEvent with usage.

References: OpenAI platform rate limits; function calling guide.

---
If a new area becomes critical, add its distilled facts here and link to an authoritative source.

## OpenAI Agents SDK (integration notes)
- Sessions: keep one SDK session per `conversation_id`. Store the session id (or state handle) and reuse for subsequent turns.
- Event mapping: map streamed partials to `TokenEvent`; summarize tool calls to `ToolStepEvent` (name, args summary, result summary); final answer to `OutputEvent`.
- Tools: wrap local capabilities (Terminal, Todo, MCP) as SDK function tools with explicit, validated schemas. Keep side‑effects idempotent.
- Handoffs: model multi‑agent flows explicitly (e.g., Triage → Specialist). Use addressing and the Bus for cross‑agent chat when needed.
- Fallback: if `OPENAI_API_KEY` is not present, use a local echo runner so Worker remains operable for E2E.

Example (basic agent run – sync):

```python
from agents import Agent, Runner

agent = Agent(
    name="DevAgent",
    instructions="Reply concisely."
)

res = Runner.run_sync(agent, "Write a haiku about recursion.")
print(res.final_output)
```

Example (Worker mapping – conceptual):

```python
for sdk_event in runner.stream_run_sdk(envelope):
    if sdk_event.type == "token":
        yield TokenEvent(conversation_id=cid, text=sdk_event.text, index=idx)
    elif sdk_event.type == "tool":
        yield ToolStepEvent(
            conversation_id=cid, name=sdk_event.name,
            args=sdk_event.args, result_summary=sdk_event.result_summary,
        )
    elif sdk_event.type == "final":
        yield OutputEvent(conversation_id=cid, text=sdk_event.text, usage=sdk_event.usage)
```

Checklist (SDK integration):
- [ ] Session store keyed by `conversation_id`
- [ ] Streamed partials surfaced promptly to SSE
- [ ] Tool schemas strict; reject invalid input early
- [ ] Backoff/retry for transient API errors; fail with clear error events
- [ ] Redact secrets from logs/events

## Redis Streams – do’s & don’ts
Do
- Use consumer groups for scalable workers; ack after successful processing
- Keep canonical UUID in the entry fields for idempotency
- Use tail reads without groups for simple fan‑out streams (SSE topic)

Don’t
- Don’t rely on exact once semantics; plan for at‑least‑once
- Don’t scan entire streams for every read; keep efficient cursors

Example (Redis CLI – group setup):

```bash
redis-cli XGROUP CREATE chat:DevAgent g1 0 MKSTREAM
redis-cli XADD chat:DevAgent * id 123 payload '{"content":"hi"}'
redis-cli XREADGROUP GROUP g1 c1 COUNT 10 STREAMS chat:DevAgent >
```

## SSE behind NGINX – minimal config

```nginx
location /stream/ {
  proxy_pass http://gateway_upstream;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering off;
  chunked_transfer_encoding on;
  proxy_read_timeout 3600s;
}
```

## pytest‑docker – responsive wait & port discovery

```python
def is_up(url: str) -> bool: ...

def test_e2e(docker_services):
    port = docker_services.port_for("gateway", 8000)
    docker_services.wait_until_responsive(
        timeout=60.0, pause=0.5,
        check=lambda: is_up(f"http://localhost:{port}/health"),
    )
```
