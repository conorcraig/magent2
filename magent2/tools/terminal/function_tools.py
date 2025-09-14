from __future__ import annotations

import importlib
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any

from magent2.observability import get_json_logger, get_metrics, get_run_context

from .tool import TerminalTool


def _format_status(result: dict[str, Any]) -> str:
    def _b(v: Any) -> str:
        return str(bool(v)).lower()

    return (
        f"ok={_b(result.get('ok'))} "
        f"exit={result.get('exit_code')} "
        f"timeout={_b(result.get('timeout'))} "
        f"truncated={_b(result.get('truncated'))}"
    )


def _success_metadata(
    cwd: str | None, command: str | None, result: dict[str, Any]
) -> dict[str, Any]:
    return {
        "cwd": cwd or "",
        "command": (command.split(" ")[0] if command else ""),
        "exit": result.get("exit_code"),
        "timeout": bool(result.get("timeout")),
        "truncated": bool(result.get("truncated")),
    }


def _build_error_metadata(
    tool: TerminalTool,
    policy: TerminalPolicy,
    command: str | None,
    cwd: str | None,
    exc: Exception,
) -> dict[str, Any]:
    allowed_count = len(policy.allowed_commands)
    allowed_sample = policy.allowed_commands[:5]

    def _resolve_cwd_effective() -> str | None:
        try:
            return tool._resolve_working_dir(cwd)
        except Exception:
            return None

    def _analyze_policy_denial() -> tuple[str | None, str | None]:
        try:
            tokens = shlex.split(command or "")
            raw = tokens[0] if tokens else ""
            cmd = os.path.basename(raw) if raw else ""
            dp = next(
                (
                    p
                    for p in (tool.deny_command_prefixes or [])
                    if (cmd.startswith(p) or raw.startswith(p))
                ),
                None,
            )
            if isinstance(exc, PermissionError):
                text = str(exc).lower()
                if dp is not None or "denied by policy" in text:
                    return "denylist", dp
                if "not allowed" in text:
                    return "allowlist", dp
            return None, dp
        except Exception:
            return None, None

    policy_reason, deny_prefix = _analyze_policy_denial()
    cwd_effective = _resolve_cwd_effective()

    return {
        "policy_reason": policy_reason,
        "allowed_count": allowed_count,
        "allowed_sample": allowed_sample,
        "deny_prefix": deny_prefix or "",
        "cwd_effective": cwd_effective or "",
        "command": (command.split(" ")[0] if command else ""),
    }


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

    # Always-apply built-in safe patterns (broader than tool-level for defense-in-depth)
    patterns += [
        # OpenAI-style keys
        r"sk-[A-Za-z0-9_-]{10,}",
        # JWTs (three base64url segments)
        r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        # Authorization headers and tokens
        r"(?i)\bAuthorization\b\s*[:=]\s*Bearer\s+[A-Za-z0-9._-]{8,}",
        r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}",
        # Generic sensitive labels
        r"(?i)(api_key|authorization|token|password|secret)\s*[:=]",
        # Long hex tokens
        r"\b[0-9a-fA-F]{32,}\b",
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
    # Optional: heuristic high-entropy token masking (conservative)
    try:
        # Very long unbroken tokens with mixed charset => likely secrets; limit false positives
        redacted = re.sub(
            r"\b(?=[A-Za-z0-9/_+=-]*[A-Z])(?=[A-Za-z0-9/_+=-]*[a-z])(?=[A-Za-z0-9/_+=-]*[0-9])[A-Za-z0-9/_+=-]{40,}\b",
            "[REDACTED]",
            redacted,
        )
    except re.error:
        pass
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
        start_ns = time.perf_counter_ns()
        logger.info(
            "tool call",
            extra={
                "event": "tool_call",
                "tool": "terminal.run",
                "attributes": {
                    "cwd": cwd or "",
                    "command": (command.split(" ")[0] if command else ""),
                    "args_len": len(command.split(" ")[1:]) if command else 0,
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

        status = _format_status(result)
        # Success log for observability
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "terminal.run",
                "attributes": _success_metadata(cwd, command, result),
            },
        )
        duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "terminal.run",
                "attributes": {
                    "exit": result.get("exit_code"),
                    "timeout": bool(result.get("timeout")),
                    "truncated": bool(result.get("truncated")),
                    "duration_ms": duration_ms,
                    "output_len": len(concise),
                },
            },
        )
        duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "terminal.run",
                "attributes": {
                    "exit": result.get("exit_code"),
                    "timeout": bool(result.get("timeout")),
                    "truncated": bool(result.get("truncated")),
                    "duration_ms": duration_ms,
                    "output_len": len(concise),
                },
            },
        )
        return f"{status}\noutput:\n{concise}"
    except Exception as exc:  # noqa: BLE001
        # Convert exceptions into a concise, redacted failure string
        msg = _redact_text(str(exc), policy.redact_substrings, policy.redact_patterns)
        concise_err = msg[: policy.function_output_max_chars]
        meta = _build_error_metadata(tool, policy, command, cwd, exc)
        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "terminal.run",
                "metadata": {"error": concise_err[:200], **meta},
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
