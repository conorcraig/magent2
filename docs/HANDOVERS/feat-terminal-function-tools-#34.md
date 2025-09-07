# Handover: Expose Terminal tool as Agents SDK function tools (#34)

Owner: next agent picking up Issue #34

## Context

- Goal: Wrap `TerminalTool` as OpenAI Agents SDK function tools with policy guardrails: allowlist, timeout, output caps, and output redaction.
- Contracts v1 are frozen; this work is scoped to tools only. Do not change event shapes or message envelopes.
- Current code:
  - `magent2/tools/terminal/tool.py` implements `TerminalTool` with allowlist, timeout, sanitized env, and byte-capped combined output.
  - Tests cover tool basics: allowlist, timeout, output cap, sanitized env (`tests/test_terminal_tool.py`).
  - Observability includes a redaction helper for logging metadata keys (`magent2/observability/__init__.py`) but not for arbitrary output text.
- References: see `docs/refs/openai-agents-sdk.md` (SDK docs index) and upstream docs site; also `docs/refs/subprocess.md`.

## Deliverables

- Agents SDK-compatible "function tools" for terminal execution that enforce policy configured via environment variables.
- Concise textual outputs for the model (redacted and truncated), separate from the lower-level byte cap in `TerminalTool`.
- Tests validating policy enforcement, truncation, and redaction behavior at the function-tool layer.

## High-level design

1. Function-tools module

- Add `magent2/tools/terminal/function_tools.py` exporting function tools that the Agents SDK can register.
- Provide a thin wrapper around `TerminalTool.run` that:
  - Loads policy from environment (safe defaults).
  - Calls `TerminalTool.run` (enforces allowlist/timeout/output byte cap).
  - Redacts sensitive substrings and regex patterns from the resulting text.
  - Produces a concise string summary for the model with an additional character cap to keep responses short.

1. Policy configuration (environment)

- Env vars (all optional; safe defaults shown):
  - `TERMINAL_ALLOWED_COMMANDS` (comma-separated). Default: empty (no commands allowed unless explicitly set). Tests set this.
  - `TERMINAL_TIMEOUT_SECONDS` (float). Default: `5.0`.
  - `TERMINAL_OUTPUT_CAP_BYTES` (int). Default: `8192` (matches `TerminalTool`).
  - `TERMINAL_FUNCTION_OUTPUT_MAX_CHARS` (int). Default: `1000` (final string returned to the model).
  - `TERMINAL_REDACT_SUBSTRINGS` (comma-separated). Default: empty.
  - `TERMINAL_REDACT_PATTERNS` (comma-separated regex). Default: include safe built-ins (see below).

- Built-in redaction patterns (always applied):
  - OpenAI-like API keys: `sk-[A-Za-z0-9_-]{10,}`
  - Common tokens/keys (case-insensitive): `api_key\s*[:=]`, `authorization\s*[:=]`, `token\s*[:=]`, `password\s*[:=]`, `secret\s*[:=]`

1. Function-tool surface (proposed)

- One primary tool to start:
  - `terminal_run(command: str, cwd: str | None = None) -> str`
    - Description: "Execute an allowed command non-interactively and return concise output."
    - Behavior: enforce allowlist/timeout; redact substrings/patterns; return short textual summary with first N characters of combined stdout/stderr; include status (`ok`, `exit`, `timeout`, `truncated`).

- Return format (string), example:
  - `"ok=true exit=0 timeout=false truncated=true\noutput:\n<redacted+truncated text>"`

- Registration with the Agents SDK:
  - Decorate or wrap as an SDK function tool (exact import path to confirm against docs; see "Open questions").
  - Export via package `__init__` for easy import into the runner wiring later.

## File changes (planned)

- Add: `magent2/tools/terminal/function_tools.py`
- Edit: `magent2/tools/terminal/__init__.py` to export `terminal_run` (and policy loader if needed)
- Add tests: `tests/test_terminal_function_tools.py`

## Implementation sketch

```python
# magent2/tools/terminal/function_tools.py
from __future__ import annotations

import os, re
from dataclasses import dataclass
from typing import Any

from .tool import TerminalTool


@dataclass
class TerminalPolicy:
    allowed_commands: list[str]
    timeout_seconds: float
    output_cap_bytes: int
    function_output_max_chars: int
    redact_substrings: list[str]
    redact_patterns: list[str]


def _load_policy_from_env() -> TerminalPolicy:
    def _split_csv(name: str) -> list[str]:
        v = os.getenv(name, "").strip()
        return [s for s in [x.strip() for x in v.split(",")] if s]

    allowed = _split_csv("TERMINAL_ALLOWED_COMMANDS")  # default: none
    timeout = float(os.getenv("TERMINAL_TIMEOUT_SECONDS", "5.0"))
    cap_bytes = int(os.getenv("TERMINAL_OUTPUT_CAP_BYTES", "8192"))
    max_chars = int(os.getenv("TERMINAL_FUNCTION_OUTPUT_MAX_CHARS", "1000"))
    substrings = _split_csv("TERMINAL_REDACT_SUBSTRINGS")
    patterns = _split_csv("TERMINAL_REDACT_PATTERNS")
    # Built-in patterns (append)
    patterns += [
        r"sk-[A-Za-z0-9_-]{10,}",
        r"(?i)(api_key|authorization|token|password|secret)\s*[:=]",
    ]
    return TerminalPolicy(
        allowed_commands=allowed,
        timeout_seconds=timeout,
        output_cap_bytes=cap_bytes,
        function_output_max_chars=max_chars,
        redact_substrings=substrings,
        redact_patterns=patterns,
    )


def _redact_text(text: str, substrings: list[str], patterns: list[str]) -> str:
    out = text
    for s in substrings:
        if s:
            out = out.replace(s, "[REDACTED]")
    for pat in patterns:
        try:
            out = re.sub(pat, "[REDACTED]", out)
        except re.error:
            # Ignore invalid patterns from config
            continue
    return out


def terminal_run(command: str, cwd: str | None = None) -> str:
    """Execute an allowed command non-interactively and return concise output.

    Returns a short, redacted, truncated string summary suitable for LLM consumption.
    """
    p = _load_policy_from_env()
    tool = TerminalTool(
        allowed_commands=p.allowed_commands,
        timeout_seconds=p.timeout_seconds,
        output_cap_bytes=p.output_cap_bytes,
    )
    res: dict[str, Any] = tool.run(command, cwd=cwd)

    combined = res.get("stdout", "")
    combined = _redact_text(combined, p.redact_substrings, p.redact_patterns)
    # Secondary char-cap for the final text returned to the model
    summary_output = combined[: p.function_output_max_chars]

    status = f"ok={bool(res.get('ok'))} exit={res.get('exit_code')} timeout={bool(res.get('timeout'))} truncated={bool(res.get('truncated'))}"
    return f"{status}\noutput:\n{summary_output}"
```

## SDK offline quick reference (OpenAI Agents SDK 0.2.11)

This section is a minimal, self-contained reference so you can wire function tools without internet:

- Imports (common):
  - `from agents import Agent` — core Agent class
  - `from agents import function_tool` — decorator to register Python functions as tools

- Define a function tool using the decorator (type hints + docstring inform schema/description):

```python
from agents import function_tool

@function_tool
def terminal_run(command: str, cwd: str | None = None) -> str:
    """Execute an allowed command non-interactively and return concise output.

    Args:
        command: Full command line (first token must be allowlisted by policy).
        cwd: Optional working directory.

    Returns:
        Short, redacted and truncated combined stdout/stderr with status line.
    """
    # call into magent2.tools.terminal.function_tools.terminal_run
    # note: keep actual logic in our wrapper so this decorated version is thin
    from magent2.tools.terminal.function_tools import terminal_run as _impl
    return _impl(command, cwd)
```

- Optional error handling: the decorator supports an error callback to transform exceptions into a tool response instead of crashing the run:

```python
from agents import function_tool

def _tool_error(e: Exception) -> str:
    return f"error: {type(e).__name__}: {e}"[:200]

@function_tool(failure_error_function=_tool_error)
def terminal_run(...):
    ...
```

- Register tools on an Agent:

```python
from agents import Agent

agent = Agent(
    name="DevAgent",
    instructions=(
        "You are a helpful developer agent. Use tools cautiously and prefer concise outputs."
    ),
    tools=[terminal_run],  # decorated function(s)
)
```

## Tests (TDD additions)

- Add `tests/test_terminal_function_tools.py` covering:
  1. Disallowed command raises/blocks via wrapper (`TERMINAL_ALLOWED_COMMANDS` unset or missing entry).
  2. Timeout respected: set `TERMINAL_TIMEOUT_SECONDS=0.5` and run a sleep command via `bash -lc 'sleep 5'`.
  3. Truncation: set small `TERMINAL_OUTPUT_CAP_BYTES` and verify `summary_output` length ≤ `TERMINAL_FUNCTION_OUTPUT_MAX_CHARS` and byte-cap is enforced by `TerminalTool`.
  4. Redaction: set `TERMINAL_REDACT_SUBSTRINGS` to include a marker printed by the command; ensure `[REDACTED]` appears and the raw token does not. Also verify built-in `sk-...` pattern masking.
  5. Optional: if SDK decorator is confirmed and lightweight, assert a function-tool schema is generated with the expected parameters (skip if SDK unavailable).

### Testing notes

- Reuse `tmp_path` and pattern from `tests/test_terminal_tool.py` where helpful.
- Use `monkeypatch.setenv` to configure env per test to avoid cross-test contamination.

## Wiring (follow-up)

- The runner wiring to actually register these function tools with a real SDK Agent is a small follow-up once Issue #33 lands. The wrappers are self-contained and can be imported and provided to the Agent configuration.

## Risks and mitigations

- SDK API surface for function tools might differ (decorator name/import path). Keep the core wrapper function independent from SDK and add the decorator in a thin layer once verified.
- Excessive outputs: enforced at two levels (byte-cap in `TerminalTool`, char-cap in returned string). Tests validate both where applicable.
- Redaction overreach: configurable via env; invalid regex patterns are ignored to avoid runtime failures.

## Validation

- Local quality gate: `just check` (format, lint, types, complexity, secrets, tests).
- Manual smoke: set `TERMINAL_ALLOWED_COMMANDS=echo` then call `terminal_run("echo hello")` from a small harness; confirm concise, redacted output and `ok=true`.

## Open questions (leave for implementor)

- Confirm the exact Agents SDK function-tool decorator and import path for version `openai-agents>=0.2.11`. Likely candidates (check docs site referenced by `docs/refs/openai-agents-sdk.md`):
  - A decorator in a `tool` or `function_schema` module.
  - A factory that wraps a Python function into a Tool object.
- Once confirmed, apply the decorator to `terminal_run` (or a thin wrapper) and export it.

## Next steps for you

1. Implement `function_tools.py` and tests per the sketch; keep contracts untouched.
2. Verify SDK decorator import path and decorate `terminal_run` accordingly.
3. Export from `magent2/tools/terminal/__init__.py` and wire into the SDK Agent in the runner once #33 is in place.
4. Validate with `just check`.
