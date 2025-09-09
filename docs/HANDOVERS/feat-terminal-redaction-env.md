# Handover: Terminal output redaction — env-driven patterns and tests

Owner: next agent
Tracking issue: https://github.com/conorcraig/magent2/issues/85

## Context

- `TerminalTool` redacts secrets with a basic built-in pattern. The function-tool wrapper (`magent2/tools/terminal/function_tools.py`) supports env-driven redaction and label-value masking, but core tool output should also be hardened to avoid accidental leaks in raw outputs.

## Deliverables

- Extend `TerminalTool` redaction to merge environment-driven configuration with safe defaults:
  - `TERMINAL_REDACT_SUBSTRINGS` (comma-separated list)
  - `TERMINAL_REDACT_PATTERNS` (comma-separated regex)
- Broaden built-in patterns to include OpenAI-style keys and common sensitive label/value pairs.
- Apply redaction before truncation to ensure no partial secret fragments are emitted.
- Tests covering default and env-driven redaction in `tests/test_terminal_safety.py` or a new `tests/test_terminal_redaction.py`.

## File references

- `magent2/tools/terminal/tool.py`
- `tests/test_terminal_tool.py` (existing)
- `tests/test_terminal_safety.py` (extend)

## Design

- Add optional constructor parameters (already exist for allow/deny); read env within `__init__` to build a list of compiled regex patterns and substrings.
- Replace `_redact_output` implementation to:
  - Apply substring replacements first → `[REDACTED]`.
  - Apply regex substitutions next.
  - Apply label-value masking for common labels (api_key, authorization, token, password, secret) without over-matching.
- Keep behavior deterministic and safe even with invalid regex patterns (ignore those with try/except around compilation).

## Tests (TDD)

- Default masking of `sk-abc1234567890`.
- Env substrings: set `TERMINAL_REDACT_SUBSTRINGS=needle` and assert `[REDACTED]` is emitted for `needle`.
- Env regex: set `TERMINAL_REDACT_PATTERNS` to a simple email pattern and assert masking.
- Label-value masking: `token=abc` → `token=[REDACTED]`.
- Ensure redaction occurs before truncation by setting a small byte cap and asserting the secret is not leaked even around boundaries.

## Acceptance criteria

- Secrets masked in outputs with default and env-driven configuration.
- Tests added pass; no regressions to existing terminal tests.
- `just check` passes locally.

## Risks & mitigations

- Over-redaction: configuration is env-driven; defaults remain conservative.
- Invalid regex patterns: ignore rather than fail.

## Branch and ownership

- Branch name: `feat/terminal-redaction-env`
- Ownership: `magent2/tools/terminal/tool.py` and tests only.