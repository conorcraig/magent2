# Implementation plan (ordered, minimal-risk)

1. Tighten core types (moved to handover)

- Tracked in `docs/HANDOVERS/feat-core-types-tightening.md`.

1. Reduce Worker idle CPU (moved to handover)

- Tracked in `docs/HANDOVERS/feat-worker-idle-backoff.md`.

1. Make Gateway SSE non‑blocking (moved to handover)

- Tracked in `docs/HANDOVERS/feat-gateway-sse-offload.md`.

1. Improve failure logging at boundaries (moved into Observability v2 handover)

- Tracked in `docs/HANDOVERS/feat-observability-wiring-v2.md`.

1. Terminal output redaction hardening (moved to handover)

- Tracked in `docs/HANDOVERS/feat-terminal-redaction-env.md`.

1. Ruff configuration improvements (moved to handover)

- Tracked in `docs/HANDOVERS/feat-ruff-config-tightening.md`.

1. Optional: tighten broad‑except (stage 2)

- [ ] Add `"BLE"` to Ruff after stage 1 is clean.
- [ ] Narrow or justify broad catches in core while keeping boundary guards; use `logger.exception` where broad catches remain.
- Acceptance:
  - [ ] No unwanted `BLE` violations in core

1. Docs alignment (moved to handover)

- Tracked in `docs/HANDOVERS/feat-docs-alignment.md`.

1. Tracking and QA

- [ ] Open GitHub issues (one per step) with clear scope and acceptance criteria; reference relevant tests.
- [ ] Validate with `just check` locally (ruff, types, complexity, secrets, tests) before PR.
- Acceptance:
  - [ ] All issues linked in PRs; `just check` passes

1. Signals and coordination (moved to handover)

- Tracked in `docs/HANDOVERS/feat-signals-v2-policy-and-sse.md`.

1. Team registry and file scope enforcement (moved to handover)

- Tracked in `docs/HANDOVERS/feat-team-registry-and-scope-enforcement.md`.

1. Worktree allocator (moved to handover)

- Tracked in `docs/HANDOVERS/feat-worktree-allocator.md`.

## Sequencing

- Do 1–4 first (runtime impact, independent).
- Do 5 with tests.
- Do 6, then 7 to avoid lint churn.
- Do 8 and 9 in parallel with review.

## Notes

- Contracts remain stable (`Bus` interface unchanged). Async/offloading paths do not alter external APIs.
- Use TDD where feasible (e.g., redaction tests first). Keep CI/readability aligned with `docs/refs/quality-gates.md`.

1. Client v1 polish (moved to handover)

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

- Tracked in `docs/HANDOVERS/feat-client-v1-polish.md`.
