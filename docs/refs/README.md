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

## Server‑Sent Events (SSE) (Gateway streaming)
- Protocol: HTTP response with Content-Type: text/event-stream. Events are lines prefixed with data: , separated by a blank line.
- Multiple data lines per event are allowed; clients concatenate them. We emit one JSON per event line.
- Keep‑alive: leave connection open; send periodic heartbeat comments if needed. Disable proxy buffering for real‑time delivery.
- Client: EventSource in browsers auto‑reconnects; you can use Last-Event-ID to resume if you emit it.
- FastAPI: use StreamingResponse(generator, media_type="text/event-stream") and ensure the generator yields strings ending with \n\n.

References: MDN SSE; FastAPI StreamingResponse; reverse proxy buffering notes.

## Model Context Protocol (MCP) (stdio JSON‑RPC)
- Framing: Each message is Content-Length: <n>\r\n, blank line, then n bytes of JSON (UTF‑8).
- Protocol: JSON‑RPC 2.0 – request has id, method, params. Response has id, result or error.
- Typical flow: initialize → tools/list → tools/call {name, arguments} → shutdown.
- Robustness: enforce read timeouts, validate Content‑Length, handle EOF; serialize requests, match responses by numeric id.

References: modelcontextprotocol.io docs/spec.

## Observability (traces, logs, metrics)
- Correlation: include conversation_id and a per‑run run_id on all logs and events.
- Tracing: start a span for Worker run; create child spans for tool calls; propagate context through Runner/Tools.
- Logging: JSON logs with minimal stable keys (timestamp, level, message, run_id, conversation_id, agent, tool).
- Metrics: counters for runs started/completed/errored; tool calls; retries; DLQ size.
- Redaction: never log secrets or full command lines with sensitive args; sanitize outputs.

References: OpenTelemetry Python; Python logging cookbook.

## Safe subprocess (Terminal tool)
- Never use shell=True; build argv with shlex.split and pass to Popen.
- Environment: start from a minimal map; set safe PATH; inject only explicit allowlisted env vars.
- Policy: enforce command allowlist (by basename), wall‑clock timeout, output byte cap; non‑interactive (no stdin).
- Termination: on timeout, kill the entire process group (e.g., os.killpg) and drain pipes.
- Sandbox: optional working directory sandbox; canonicalize cwd/paths; deny path escapes if policy requires.

References: Python subprocess docs; OWASP Command Injection.

## Docker + pytest‑docker (E2E)
- Compose healthcheck gates service readiness; tests should wait until responsive (e.g., HTTP /health) before proceeding.
- Avoid fixed host ports in tests; discover host port via docker_services.port_for(service, internal_port).
- Keep a single compose file as source of truth; parameterize ports with env vars for local pinning if needed.

References: pytest‑docker docs; Compose healthcheck docs.

## Quality gates (Ruff, mypy, secrets, complexity)
- Ruff: checks + formatter; keep config minimal and project‑wide.
- mypy: strict where feasible (no implicit Optional, disallow untyped defs); manage baselines for legacy code.
- detect‑secrets: maintain a baseline; run in pre‑commit and CI; never commit real secrets.
- Xenon/Radon: set complexity thresholds; fail CI when functions exceed target score; ratchet down over time.

References: Ruff, mypy, detect‑secrets, Xenon/Radon docs.

## uv + GitHub Actions (CI)
- Use uv to install/sync; cache uv downloads/venv to speed builds.
- Cancel in‑progress jobs per branch to reduce churn; emit JUnit/JSON reports as artifacts.
- Keep CI steps: ruff check/format (dry run), mypy, pytest (unit/e2e), secrets scan.

References: uv + GitHub Actions guide; GHA caching docs.

## OpenAI platform (general)
- Rate limits: plan retry/backoff for model calls; consider token budgets.
- Function/tool calling: define strict JSON schemas; validate inputs server‑side; keep tools idempotent where possible.
- Streaming: prefer streamed responses for UX; map partials to TokenEvent; emit a final OutputEvent with usage.

References: OpenAI platform rate limits; function calling guide.

---
If a new area becomes critical, add its distilled facts here and link to an authoritative source.
