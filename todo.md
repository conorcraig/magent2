# Implementation plan (ordered, minimal-risk)

1. Tighten core types

- [ ] Change `magent2/bus/interface.py` `BusMessage.payload: dict` → `dict[str, Any]`.
- [ ] Fix any callsites/types as needed; keep contracts stable.
- Acceptance:
  - [ ] `uv run mypy` clean for changed files
  - [ ] `uv run pytest -q` green

1. Reduce Worker idle CPU (no interface change)

- Option A (quick):
  - [ ] In `magent2/worker/__main__.py`, after `worker.process_available()`, if it returns 0, sleep with small backoff (start ~50ms, cap ~200ms).
- Option B (preferred, still interface‑safe):
  - [ ] Extend `magent2/bus/redis_adapter.RedisBus` to accept `block_ms: int | None = None` and use it for `xreadgroup(..., block=block_ms)` when a group is configured.
  - [ ] In `magent2/worker/__main__.py`, construct `RedisBus(redis_url=..., group_name="magent2", consumer_name=<uuid>, block_ms=1000)` and acknowledge after publish (already implemented).
- Acceptance:
  - [ ] Worker uses near‑zero CPU when idle
  - [ ] Tests unaffected; e2e still works

1. Make Gateway SSE non‑blocking

- [ ] In `magent2/gateway/app.py` `event_gen()`, offload sync reads via `await asyncio.to_thread(bus.read, topic, last_id=..., limit=...)`.
- [ ] Keep small sleep only when empty; skip sleep after yields.
- Acceptance:
  - [ ] `/stream/{conversation_id}` remains responsive under load
  - [ ] No event‑loop stalls; existing tests pass

1. Improve failure logging at boundaries

- [ ] In `magent2/worker/worker.py` exception path inside `_run_and_stream`, use `logger.exception(..., extra=...)` (level error) and keep metrics.
- [ ] In `magent2/gateway/app.py` publish/ready error paths, log at error before raising `HTTPException`.
- [ ] In `magent2/tools/mcp/gateway.py close()` and similar cleanup, log at debug instead of silent `pass`.
- Acceptance:
  - [ ] Stack traces captured for unexpected errors
  - [ ] Tests still pass (no noisy logs in tests)

1. Terminal output redaction hardening

- [ ] In `magent2/tools/terminal/tool.py`, broaden default redaction patterns (Bearer tokens, cloud keys) and reuse sensitive key hints from `magent2/observability`.
- [ ] Support env‑driven redaction per README (`TERMINAL_REDACT_SUBSTRINGS`, `TERMINAL_REDACT_PATTERNS`) merged with defaults.
- [ ] Add tests in `tests/test_terminal_tool.py` covering default and env‑driven redaction.
- Acceptance:
  - [ ] Secrets masked in outputs
  - [ ] New tests pass

1. Ruff configuration improvements (stage 1: low‑churn rules)

- [ ] In `pyproject.toml [tool.ruff.lint] select`, add `"B","S","T20"` (Bugbear, basic security, no print in lib code).
- [ ] Fix violations in library code; allow prints in `scripts/` and `tests/` via directory scoping rather than inline ignores.
- Acceptance:
  - [ ] `uv run ruff check` clean

1. Optional: tighten broad‑except (stage 2)

- [ ] Add `"BLE"` to Ruff after stage 1 is clean.
- [ ] Narrow or justify broad catches in core while keeping boundary guards; use `logger.exception` where broad catches remain.
- Acceptance:
  - [ ] No unwanted `BLE` violations in core

1. Docs alignment

- [ ] Update `docs/refs/sse.md` with `asyncio.to_thread` pattern and heartbeat guidance.
- [ ] Update `docs/refs/redis-streams.md` to mention optional blocking reads via adapter param.
- [ ] Ensure README “Terminal tool (policy via env)” matches implemented redaction env vars.
- Acceptance:
  - [ ] Docs match behavior; links/current examples verified

1. Tracking and QA

- [ ] Open GitHub issues (one per step) with clear scope and acceptance criteria; reference relevant tests.
- [ ] Validate with `just check` locally (ruff, types, complexity, secrets, tests) before PR.
- Acceptance:
  - [ ] All issues linked in PRs; `just check` passes

1. Signals and coordination

- [ ] Add `signal_send`/`signal_recv` events to SSE stream; include `topic`, `message_id`, payload length.
- [ ] Implement `signal_wait_any(topics[])` and `signal_wait_all(topics[])` tools.
- [ ] Add topic namespace and allowlist policy (e.g., `signal:<team>/...`); enforce in tools.
- [ ] Add per-topic payload caps, redaction of sensitive keys, and rate limits.
- [ ] Persist `last_id` cursor in session for long waits and reliability across restarts.
- Acceptance:
  - [ ] SSE shows signal events in live runs
  - [ ] wait_any/wait_all covered by unit tests
  - [ ] Policy enforced; violations return actionable errors

1. Team registry and file scope enforcement

- [ ] Define registry schema: agents, owners, window person, allowed file scopes, worktrees.
- [ ] Enforce allowed file scopes in files tool and terminal tool.
- Acceptance:
  - [ ] Attempts outside scope are denied with clear reason
  - [ ] Unit tests for scope enforcement pass

1. Worktree allocator

- [ ] Create per-agent git worktree manager (create/cleanup; branch naming).
- [ ] Map allowed file scopes to worktree; preflight conflicts.
- Acceptance:
  - [ ] Worktrees created per agent; cleanup verified
  - [ ] Conflict preflight documented and tested

## Sequencing

- Do 1–4 first (runtime impact, independent).
- Do 5 with tests.
- Do 6, then 7 to avoid lint churn.
- Do 8 and 9 in parallel with review.

## Notes

- Contracts remain stable (`Bus` interface unchanged). Async/offloading paths do not alter external APIs.
- Use TDD where feasible (e.g., redaction tests first). Keep CI/readability aligned with `docs/refs/quality-gates.md`.

1. Client v1 polish: `scripts/client.py` UX, modes, reliability

- [ ] Add CLI flags
  - [ ] `--log-level {debug,info,warning,error}`
  - [ ] `--quiet` (print only final output)
  - [ ] `--json` (one JSON object per SSE event per line)
  - [ ] `--max-events N` (pass-through to `/stream/...`)
  - [ ] Optional: `--color {auto,always,never}` override
- [ ] Output polish (TTY-aware via `rich`)
  - [ ] Colorize only when `stdout.isatty()` and not in `--json/--quiet`
  - [ ] Tokens: dim "AI> " + cyan text; tool steps: yellow; errors: red to stderr
  - [ ] Keep plain text in non-TTY/JSON modes (compatibility with tests/scripts)
- [ ] Error handling and exit codes
  - [ ] Non-2xx `/send` → concise stderr + non-zero exit
  - [ ] Exit codes: `0` ok, `2` timeout, `3` send failed, `4` stream connect failed, `5` usage
  - [ ] Document exit codes in `--help` and README
- [ ] Stream reliability
  - [ ] Use `httpx.Timeout(connect=5.0, read=None)` for fast fail connect + infinite read
  - [ ] Reconnect with capped exponential backoff + jitter (base≈0.5s, cap≈5s)
  - [ ] Parse `created_at` timestamps for stale-cutoff (one-shot); fail-open if missing
- [ ] Base URL discovery and preflight
  - [ ] On `--base-url auto`, probe `/health` once; friendly error on failure
- [ ] Internal structure
  - [ ] Small dispatcher: event → render
  - [ ] Separate stream parse from render (simplifies `--json`)
  - [ ] Optional: best-effort Pydantic validation for known events in `--json`
- [ ] Contract alignment / cleanup
  - [ ] Keep `token`, `tool_step`, `output`, `log` (gated by `--log-level`)
  - [ ] Consider dropping `user_message` print (not emitted in v1); document if kept
- [ ] Tests
  - [ ] Send failure → non-zero + stderr
  - [ ] `--quiet` prints only final output
  - [ ] `--json` prints one JSON per event line
  - [ ] Backoff loop doesn’t tight-spin on repeated failures
  - [ ] Stale-cutoff uses parsed timestamps
- [ ] Docs
  - [ ] README usage for `--quiet`, `--json`, `--log-level`, `--max-events`, color behavior
  - [ ] Short "scripting patterns" (capture only final output, etc.)

- Acceptance:
  - [ ] New flags work as specified; help text includes exit codes
  - [ ] Human output is colorized on TTY; plain on non-TTY/JSON
  - [ ] `/send` failures return code `3`; timeouts return `2`
  - [ ] Streaming uses connect timeout + backoff; no tight loops observed in tests
  - [ ] One-shot stale cutoff robust to missing/invalid timestamps
  - [ ] Tests added for modes and failure paths; all tests pass
  - [ ] README updated with examples and scripting tips
