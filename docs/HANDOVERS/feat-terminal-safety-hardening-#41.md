# Handover: Security hardening for TerminalTool (#41)

Owner: next agent picking up Issue #41

## Context

- Goal: Strengthen `TerminalTool` safety with a deny‑list of dangerous commands/flags, enforce sandbox cwd policy and deny path escapes, and add tests for redaction/denial cases.
- Current code: `magent2/tools/terminal/tool.py` implements allowlist, timeout, sanitized env, and byte‑capped output. Tests exist in `tests/test_terminal_tool.py`.
- References: `docs/refs/subprocess.md`, `docs/refs/quality-gates.md`.

## Deliverables

- Deny‑list enforcement layered on top of allowlist (deny wins).
- Sandbox: when a `sandbox_cwd` is configured, reject any execution attempting to escape that directory (e.g., with `..` or absolute paths when disallowed).
- Redaction: tests to ensure sensitive tokens are redacted in outputs at the function‑tool layer where applicable.

## Design

- Policy extension:
  - Add optional env vars (or config args) to `TerminalTool` and/or wrapper:
    - `TERMINAL_DENY_COMMANDS` (comma‑separated prefixes, e.g., `rm,dd,mkfs,sudo,ssh,scp,chmod,chown`)
    - `TERMINAL_SANDBOX_CWD` (path). If set, normalize and enforce child‑path under this root.
  - Deny check runs before allowlist resolution; provide clear error messages.

- Path policy:
  - When `cwd` is provided, resolve `realpath` against `sandbox_cwd` and fail if it escapes.
  - For command arguments that include path tokens, optionally validate they remain under sandbox (best‑effort parse of flags like `-o`, `-f`, etc.). Keep conservative.

## Tests (TDD)

- Add `tests/test_terminal_safety.py`:
  - Deny‑list: set `TERMINAL_DENY_COMMANDS=rm`, allowlist includes `rm`; ensure execution is blocked with a clear message.
  - Sandbox: set `TERMINAL_SANDBOX_CWD` to a tmp dir; attempts to run with `cwd=tmp/..` or paths outside are rejected.
  - Redaction: run a command that prints a fake secret like `sk-abc1234567890` and assert output replaces it with `[REDACTED]` at function‑tool wrapper level if applicable.

## File references

- `magent2/tools/terminal/tool.py`
- `tests/test_terminal_tool.py`

## Validation

- `uv run pytest -q tests/test_terminal_safety.py`
- `just check`

## Risks

- Over‑blocking benign commands → document deny‑list defaults and allow project overrides.
- Path parsing for arguments is heuristic; start with cwd sandbox enforcement, then iterate.
