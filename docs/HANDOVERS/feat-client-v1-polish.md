# Handover: Client v1 polish — flags, JSON mode, backoff, docs

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/77>

## Context

- The CLI client (`scripts/client.py`) already supports one‑shot mode, token/tool/log rendering, and basic reconnects.
- The PRD and internal plan call for additional flags, reliability tweaks, and README docs.

## Deliverables

- CLI flags:
  - `--log-level {debug,info,warning,error}` (honor in `StreamPrinter`)
  - `--quiet` (print only final output line)
  - `--json` (print one JSON per SSE event per line)
  - `--max-events N` (pass through to `/stream/...`)
- Output polish (TTY‑aware):
  - Colorize only when `stdout.isatty()` and not in `--json/--quiet` (optional via `rich` later; start plain).
- Reliability:
  - Use `httpx.Timeout(connect=5.0, read=None)` for streaming; reconnect with capped exponential backoff + jitter.
  - If available, filter events older than a cutoff in one‑shot mode (already implemented).
- Exit codes:
  - `0` ok, `2` timeout, `3` send failed, `4` stream connect failed, `5` usage.
- README updates documenting flags and usage examples.

## File references

- `scripts/client.py`
- `README.md`
- Tests: `tests/test_client_one_shot.py`, `tests/test_client_log_events.py` (extend/add)

## Design notes

- Add new args in `parse_args` and thread through to `ClientConfig`.
- Implement a `RenderMode` that switches between pretty, quiet, and json renderers inside `StreamPrinter._handle_*`.
- Respect `--max-events` by adding a query param to the stream URL and stopping after N payloads.

## Tests (offline)

- Add `tests/test_client_modes.py` covering `--quiet` and `--json` basic behavior with mocked SSE.
- Extend one‑shot tests to assert exit codes and max‑events behavior.

## Acceptance criteria

- New flags work as specified; help text includes exit codes.
- Human output is plain by default; JSON mode emits one compact JSON per event line.
- Tests pass; `just check` green.

## Risks & mitigations

- SSE timing: use deterministic mocks and avoid real sleeps in tests.

## Branch and ownership

- Branch name: `feat/client-v1-polish`
- Ownership: restricted to `scripts/client.py`, new tests, and README changes (docs only).
