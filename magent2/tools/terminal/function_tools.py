from __future__ import annotations

import os
import re
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


def _split_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    value = value.strip()
    if not value:
        return []
    return [s for s in (x.strip() for x in value.split(",")) if s]


def _load_policy_from_env() -> TerminalPolicy:
    allowed = _split_csv_env("TERMINAL_ALLOWED_COMMANDS")
    timeout = float(os.getenv("TERMINAL_TIMEOUT_SECONDS", "5.0"))
    cap_bytes = int(os.getenv("TERMINAL_OUTPUT_CAP_BYTES", "8192"))
    max_chars = int(os.getenv("TERMINAL_FUNCTION_OUTPUT_MAX_CHARS", "1000"))
    substrings = _split_csv_env("TERMINAL_REDACT_SUBSTRINGS")
    patterns = _split_csv_env("TERMINAL_REDACT_PATTERNS")

    # Always-apply built-in safe patterns
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
    redacted = text
    for s in substrings:
        if s:
            redacted = redacted.replace(s, "[REDACTED]")
    for pat in patterns:
        try:
            redacted = re.sub(pat, "[REDACTED]", redacted)
        except re.error:
            # Ignore invalid regex patterns from configuration
            continue
    return redacted


def terminal_run(command: str, cwd: str | None = None) -> str:
    """Execute an allowed command non-interactively and return concise output.

    The underlying `TerminalTool` enforces the allowlist, timeout, and byte-cap policies.
    This wrapper additionally redacts sensitive data and truncates the returned text
    to a compact length suitable for LLM consumption.
    """
    policy = _load_policy_from_env()

    tool = TerminalTool(
        allowed_commands=policy.allowed_commands,
        timeout_seconds=policy.timeout_seconds,
        output_cap_bytes=policy.output_cap_bytes,
    )

    result: dict[str, Any] = tool.run(command, cwd=cwd)

    combined = result.get("stdout", "")
    combined = _redact_text(combined, policy.redact_substrings, policy.redact_patterns)
    concise = combined[: policy.function_output_max_chars]

    def _b(v: Any) -> str:
        return str(bool(v)).lower()

    status = (
        f"ok={_b(result.get('ok'))} "
        f"exit={result.get('exit_code')} "
        f"timeout={_b(result.get('timeout'))} "
        f"truncated={_b(result.get('truncated'))}"
    )
    return f"{status}\noutput:\n{concise}"


__all__ = [
    "terminal_run",
]

# Optional: expose as an Agents SDK function tool if the decorator is available
try:
    from agents import function_tool as _function_tool  # type: ignore
except Exception:  # noqa: BLE001
    _function_tool = None  # type: ignore[assignment]

if _function_tool is not None:
    @_function_tool  # type: ignore[misc]
    def terminal_run_tool(command: str, cwd: str | None = None) -> str:
        """Agents SDK function tool wrapper for `terminal_run`.

        This thin wrapper defers to the local implementation to keep policy logic
        independent from the SDK. It is decorated only when the SDK is present.
        """
        return terminal_run(command, cwd)

    __all__.append("terminal_run_tool")

