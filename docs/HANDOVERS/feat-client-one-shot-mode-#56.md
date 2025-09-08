# Handover: Extend client with non-interactive one-shot mode (#56)

Owner: next agent picking up Issue #56

## Context

- Goal: Add a non-interactive path to `scripts/client.py` that sends one message and streams until the final `OutputEvent`, printing tokens and tool steps along the way, then exits with a clear status code.
- Current client: `scripts/client.py` implements a REPL with SSE streaming via `StreamPrinter` and has a `one_shot(...)` path scaffolding in place (see functions `parse_args`, `one_shot`, and class `StreamPrinter`).
- Gateway API: `magent2/gateway/app.py` exposes `/send` (POST) and `/stream/{conversation_id}` (SSE), already used by the client.
- References: `docs/refs/sse.md`, `docs/refs/quality-gates.md`.

## Deliverables

- CLI flag `--message "..."` to trigger one-shot mode and `--timeout <seconds>` to bound waiting.
- In one-shot mode:
  - Start SSE stream first; ignore stale events older than a captured timestamp.
  - Print tokens inline, `[tool]` steps, and final output; exit 0 on success, non-zero on timeout or HTTP errors.
- Ruff/mypy clean; tests for one-shot behavior using offline mocks.

## High-level steps

1. Argument parsing

- Ensure `parse_args` supports `--message` and `--timeout` (present in code) and that `main` calls `one_shot(...)` when `--message` is supplied.

1. One-shot streaming

- In `one_shot`, set a `since` cutoff timestamp before sending to filter stale events (`StreamPrinter._since_iso`).
- Start `StreamPrinter`, call `_send_message`, then `wait_for_final(timeout)` and exit accordingly.

1. Event rendering

- `StreamPrinter` already supports events: `token`, `tool_step`, `output`, and `log`. Keep REPL behavior unchanged when `--message` is not provided.

## Tests (offline, no network)

- Add `tests/test_client_one_shot.py`:
  - Monkeypatch `httpx.stream` to yield a synthetic SSE sequence: `user_message` (optional), `token` chunks, and an `output` event.
  - Monkeypatch `httpx.post` for `/send` to return 200.
  - Assert `one_shot(cfg, message, timeout)` returns 0 and prints expected lines (use `capsys`).
  - Add a timeout case: no `output` event → returns non-zero and prints a timeout message.

## File references

- `scripts/client.py` (entrypoint, CLI, SSE printer)
- `magent2/gateway/app.py` (endpoints)
- `docs/refs/sse.md` (SSE format guidance)

## Validation

- Local: `uv run pytest -q tests/test_client_one_shot.py`
- Quality gate: `just check`

## Risks

- Flaky SSE timing in tests → use deterministic mock streams; avoid real sleep where possible.
- Terminal rendering in CI → guard spinner usage and color by `isatty()` (already handled).
