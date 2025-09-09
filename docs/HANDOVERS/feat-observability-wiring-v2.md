# Handover: Observability wiring v2 — structured logs and counters across paths

Owner: next agent

## Context

- Observability helpers exist in `magent2/observability/__init__.py`: structured JSON logs, run context, counters.
- We want consistent correlation fields (`run_id`, `conversation_id`, `agent`) and counters: runs started/completed/errored, tool calls/errors.
- Worker already uses `use_run_context` and counters; extend consistency across tool wrappers and gateway.

## Deliverables

- Ensure tool wrappers (terminal/chat/todo/signals) log `tool_call`/`tool_error` with correlation fields and increment counters via `get_metrics().increment(...)`.
- Gateway:
  - Log send/stream interactions with correlation fields when possible.
  - Map errors to appropriate HTTP statuses (already present for send).
- Add a test `tests/test_observability_wiring.py` asserting logs include correlation fields and counters increment in a small end‑to‑end run with a fake runner.

- Boundary failure logging (align with existing plan):
  - In `magent2/worker/worker.py` exception path inside `_run_and_stream`, use `logger.exception(..., extra=...)` (level error) and keep metrics.
  - In `magent2/gateway/app.py` publish/ready error paths, log at error before raising `HTTPException`.
  - In `magent2/tools/mcp/gateway.py` `close()` and similar cleanup, log at debug instead of silent `pass`.

## File references

- `magent2/tools/*/*` wrappers
- `magent2/gateway/app.py`
- `tests/test_observability_wiring.py`

## Design notes

- Use `get_json_logger("magent2")` and `get_metrics()`.
- For correlation in tools, prefer `get_run_context()`; for gateway, correlation may be absent; keep logs minimal.

## Acceptance criteria

- Logs emitted at tool call/error have correlation fields when context is set.
- Metrics include increments for tool calls and errors across at least two tools in tests.
- Tests and `just check` pass.

## Risks

- Over‑logging; keep messages short and keys stable.

## Branch and ownership

- Branch name: `feat/observability-wiring-v2`
- Ownership: small, localized edits in wrappers and gateway; avoid touching core contracts.
