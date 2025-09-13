from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
import os
import shlex
from typing import Any

from magent2.observability import get_json_logger, get_metrics, get_run_context

from .tool import TerminalTool


@dataclass(slots=True)
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


def _redact_label_values(text: str) -> str:
    """Redact values that follow common sensitive labels while preserving labels.

    Examples matched: "api_key: VALUE", "token=VALUE".
    """
    patterns = [
        r"(?i)\b(api_key|authorization|token|password|secret)\b\s*[:=]\s*\S+",
    ]
    redacted = text
    for pat in patterns:
        try:
            redacted = re.sub(
                pat,
                lambda m: re.sub(r"([:=]\s*)\S+", r"\1[REDACTED]", m.group(0)),
                redacted,
            )
        except re.error:
            continue
    return redacted


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
    # Also redact values attached to sensitive labels
    redacted = _redact_label_values(redacted)
    return redacted


def terminal_run(command: str, cwd: str | None = None) -> str:
    """Execute an allowed command non-interactively and return concise output.

    The underlying `TerminalTool` enforces the allowlist, timeout, and byte-cap policies.
    This wrapper additionally redacts sensitive data and truncates the returned text
    to a compact length suitable for LLM consumption.
    """
    policy = _load_policy_from_env()
    logger = get_json_logger("magent2.tools")
    metrics = get_metrics()
    ctx = get_run_context() or {}

    tool = TerminalTool(
        allowed_commands=policy.allowed_commands,
        timeout_seconds=policy.timeout_seconds,
        output_cap_bytes=policy.output_cap_bytes,
    )

    try:
        logger.info(
            "tool call",
            extra={
                "event": "tool_call",
                "tool": "terminal.run",
                "metadata": {
                    "cwd": cwd or "",
                    "command": command.split(" ")[0] if command else "",
                },
            },
        )
        metrics.increment(
            "tool_calls",
            {
                "tool": "terminal",
                "conversation_id": str(ctx.get("conversation_id", "")),
                "run_id": str(ctx.get("run_id", "")),
            },
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
        # Success log for observability
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "terminal.run",
                "metadata": {
                    "cwd": cwd or "",
                    "command": command.split(" ")[0] if command else "",
                    "exit": result.get("exit_code"),
                    "timeout": bool(result.get("timeout")),
                    "truncated": bool(result.get("truncated")),
                },
            },
        )
        return f"{status}\noutput:\n{concise}"
    except Exception as exc:  # noqa: BLE001
        # Convert exceptions into a concise, redacted failure string
        msg = _redact_text(str(exc), policy.redact_substrings, policy.redact_patterns)
        concise_err = msg[: policy.function_output_max_chars]
        # Enrich terminal policy context for denials
        policy_reason: str | None = None
        deny_prefix: str | None = None
        allowed_count = len(policy.allowed_commands)
        allowed_sample = policy.allowed_commands[:5]
        cwd_effective: str | None = None
        try:
            # Resolve effective cwd if sandboxing applies (best effort)
            cwd_effective = getattr(tool, "_resolve_working_dir")(cwd)  # type: ignore[misc]
        except Exception:
            cwd_effective = None
        try:
            tokens = shlex.split(command or "")
            raw = tokens[0] if tokens else ""
            cmd = os.path.basename(raw) if raw else ""
            # Detect denylist match
            for prefix in getattr(tool, "deny_command_prefixes", []) or []:
                if (cmd and cmd.startswith(prefix)) or (raw and raw.startswith(prefix)):
                    deny_prefix = prefix
                    break
            if isinstance(exc, PermissionError):
                text = str(exc).lower()
                if deny_prefix is not None or "denied by policy" in text:
                    policy_reason = "denylist"
                elif "not allowed" in text:
                    policy_reason = "allowlist"
        except Exception:
            # Best-effort enrichment only
            pass

        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "terminal.run",
                "metadata": {
                    "error": concise_err[:200],
                    "policy_reason": policy_reason,
                    "allowed_count": allowed_count,
                    "allowed_sample": allowed_sample,
                    "deny_prefix": deny_prefix or "",
                    "cwd_effective": cwd_effective or "",
                    "command": (command.split(" ")[0] if command else ""),
                },
            },
        )
        metrics.increment(
            "tool_errors",
            {
                "tool": "terminal",
                "conversation_id": str(ctx.get("conversation_id", "")),
                "run_id": str(ctx.get("run_id", "")),
            },
        )
        return "ok=false exit=None timeout=false truncated=false\nerror:\n" + concise_err


__all__ = [
    "terminal_run",
]


def _maybe_get_function_tool() -> Any | None:
    """Return the Agents SDK function_tool decorator if available."""
    try:
        module = importlib.import_module("agents")
    except Exception:  # noqa: BLE001
        return None
    return getattr(module, "function_tool", None)


# Optional: expose as an Agents SDK function tool if the decorator is available
_function_tool = _maybe_get_function_tool()

if _function_tool is not None:

    @_function_tool
    def terminal_run_tool(command: str, cwd: str | None = None) -> str:
        """Agents SDK function tool wrapper for `terminal_run`.

        This thin wrapper defers to the local implementation to keep policy logic
        independent from the SDK. It is decorated only when the SDK is present.
        """
        return terminal_run(command, cwd)

    __all__.append("terminal_run_tool")
