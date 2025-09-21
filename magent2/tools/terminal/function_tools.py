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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _log_tool_call(logger: Any, cwd: str | None, command: str | None, cmd_for_log: str) -> None:
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool": "terminal.run",
            "attributes": {
                "cwd": cwd or "",
                "command": (command.split(" ")[0] if command else ""),
                "cmd": cmd_for_log,
                "args_len": len(command.split(" ")[1:]) if command else 0,
            },
        },
    )


def _metrics_increment(metrics: Any, name: str, ctx: dict[str, Any]) -> None:
    metrics.increment(
        name,
        {
            "tool": "terminal",
            "conversation_id": str(ctx.get("conversation_id", "")),
            "run_id": str(ctx.get("run_id", "")),
        },
    )


def _resolve_cwd_effective(tool: TerminalTool, cwd: str | None) -> str | None:
    try:
        return tool._resolve_working_dir(cwd)
    except Exception:
        return None


def _extract_cmd_parts(command: str | None) -> tuple[str, str]:
    try:
        tokens = shlex.split(command or "")
        raw = tokens[0] if tokens else ""
        cmd = os.path.basename(raw) if raw else ""
        return raw, cmd
    except Exception:
        return "", ""


def _find_deny_prefix(tool: TerminalTool, raw: str, cmd: str) -> str | None:
    try:
        for prefix in tool.deny_command_prefixes or []:
            if cmd.startswith(prefix) or raw.startswith(prefix):
                return prefix
        return None
    except Exception:
        return None


def _classify_policy_reason(deny_prefix: str | None, exc: Exception) -> str | None:
    if not isinstance(exc, PermissionError):
        return None
    try:
        text = str(exc).lower()
    except Exception:
        text = ""
    if deny_prefix is not None or "denied by policy" in text:
        return "denylist"
    if "not allowed" in text:
        return "allowlist"
    return None


def _detect_policy_context(
    tool: TerminalTool, command: str | None, exc: Exception
) -> tuple[str | None, str | None]:
    raw, cmd = _extract_cmd_parts(command)
    deny_prefix = _find_deny_prefix(tool, raw, cmd)
    reason = _classify_policy_reason(deny_prefix, exc)
    return reason, deny_prefix


def _build_error_metadata(
    tool: TerminalTool,
    policy: TerminalPolicy,
    command: str | None,
    cwd: str | None,
    exc: Exception,
) -> dict[str, Any]:
    allowed_count = len(policy.allowed_commands)
    allowed_sample = policy.allowed_commands[:5]
    policy_reason, deny_prefix = _detect_policy_context(tool, command, exc)
    cwd_effective = _resolve_cwd_effective(tool, cwd)
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


# Sensible defaults for development and CI environments
DEFAULT_ALLOWED_COMMANDS = [
    # Core utilities
    "bash",
    "echo",
    "printf",
    "cat",
    "ls",
    "head",
    "tail",
    "find",
    "grep",
    "xargs",
    # Text processing
    "sed",
    "awk",
    "tr",
    "cut",
    "sort",
    "uniq",
    "tee",
    "wc",
    # File info
    "stat",
    "file",
    "base64",
    "md5sum",
    "sha256sum",
    # System info
    "ps",
    "date",
    "env",
    "whoami",
    "id",
    "uname",
    "df",
    "du",
    "free",
    "top",
    "history",
    # Control flow
    "sleep",
    "timeout",
    "nohup",
    "which",
    "type",
    # Development tools
    "python3",
    "git",
    "curl",
    "wget",
    "jq",
    "node",
    "npm",
    "docker",
    # File operations
    "tar",
    "zip",
    "unzip",
    "cp",
    "mv",
    "ln",
    "mkdir",
    "touch",
    "chmod",
]


def _split_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    value = value.strip()
    if not value:
        return []
    return [s for s in (x.strip() for x in value.split(",")) if s]


# One-time policy cache (process lifetime)
_POLICY_CACHE: TerminalPolicy | None = None


def _load_policy_from_env() -> TerminalPolicy:
    # Use built-in defaults unless explicitly overridden
    env_allowed = os.getenv("TERMINAL_ALLOWED_COMMANDS")
    if env_allowed is not None:
        # Explicitly set (could be empty string to deny all)
        allowed = _split_csv_env("TERMINAL_ALLOWED_COMMANDS")
    else:
        # Not set - use defaults
        allowed = DEFAULT_ALLOWED_COMMANDS.copy()

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

    # Cache once per process to prevent runtime policy flips in unattended agents
    global _POLICY_CACHE
    if _POLICY_CACHE is None:
        _POLICY_CACHE = TerminalPolicy(
            allowed_commands=allowed,
            timeout_seconds=timeout,
            output_cap_bytes=cap_bytes,
            function_output_max_chars=max_chars,
            redact_substrings=substrings,
            redact_patterns=patterns,
        )
    return _POLICY_CACHE


def _reset_terminal_policy_cache_for_tests() -> None:
    """Testing-only: reset the cached policy to allow per-test env changes.

    Do not call this in production code. Use only from tests.
    """
    global _POLICY_CACHE
    _POLICY_CACHE = None


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
        redacted_cmd_full = _redact_text(
            command or "", policy.redact_substrings, policy.redact_patterns
        )
        cmd_for_log = _truncate(redacted_cmd_full, 160)
        _log_tool_call(logger, cwd, command, cmd_for_log)
        _metrics_increment(metrics, "tool_calls", ctx)

        result: dict[str, Any] = tool.run(command, cwd=cwd)

        combined = _redact_text(
            result.get("stdout", ""), policy.redact_substrings, policy.redact_patterns
        )
        concise = combined[: policy.function_output_max_chars]

        status = _format_status(result)
        duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        success_attrs = {
            **_success_metadata(cwd, command, result),
            "duration_ms": duration_ms,
            "output_len": len(concise),
            "cmd": cmd_for_log,
            "cwd": cwd or "",
        }
        logger.info(
            "tool success",
            extra={
                "event": "tool_success",
                "tool": "terminal.run",
                "attributes": success_attrs,
            },
        )
        return f"{status}\noutput:\n{concise}"
    except Exception as exc:  # noqa: BLE001
        # Convert exceptions into a concise, redacted failure string
        msg = _redact_text(str(exc), policy.redact_substrings, policy.redact_patterns)
        concise_err = msg[: policy.function_output_max_chars]
        meta = _build_error_metadata(tool, policy, command, cwd, exc)
        meta.update(
            {
                "cmd": _redact_text(
                    command or "", policy.redact_substrings, policy.redact_patterns
                )[:160],
                "cwd": cwd or "",
            }
        )
        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "terminal.run",
                "metadata": {"error": concise_err[:200], **meta},
            },
        )
        _metrics_increment(metrics, "tool_errors", ctx)
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
