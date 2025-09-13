from __future__ import annotations

import os
import re
import shlex
import signal
import time
from collections.abc import Iterable
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from typing import Any


def _truncate_to_bytes(text: str, limit_bytes: int) -> tuple[str, bool]:
    data = text.encode("utf-8", errors="ignore")
    if len(data) <= limit_bytes:
        return text, False
    truncated = data[:limit_bytes]
    return truncated.decode("utf-8", errors="ignore"), True


class TerminalTool:
    """Safe terminal execution tool with allowlist, timeout, and output caps.

    Parameters
    ----------
    allowed_commands:
        List of command basenames allowed to execute (e.g., "bash", "python3", "echo").
    timeout_seconds:
        Maximum wall time for a command before it is forcefully terminated.
    output_cap_bytes:
        Maximum number of bytes to retain from combined stdout+stderr output.
    extra_env:
        Extra environment variables to inject into the sanitized environment.
    """

    def __init__(
        self,
        *,
        allowed_commands: list[str] | None = None,
        timeout_seconds: float = 5.0,
        output_cap_bytes: int = 8_192,
        extra_env: dict[str, str] | None = None,
        deny_commands: list[str] | None = None,
        sandbox_cwd: str | None = None,
    ) -> None:
        self.allowed_commands: list[str] = allowed_commands or []
        self.timeout_seconds: float = timeout_seconds
        self.output_cap_bytes: int = output_cap_bytes
        self.extra_env: dict[str, str] = extra_env or {}
        # Policy from args or environment
        env_deny = os.getenv("TERMINAL_DENY_COMMANDS", "").strip()
        self.deny_command_prefixes: list[str] = (
            [s for s in (p.strip() for p in env_deny.split(",")) if s]
            if deny_commands is None
            else deny_commands
        )
        env_sandbox = os.getenv("TERMINAL_SANDBOX_CWD")
        self._sandbox_root: Path | None = (
            Path(sandbox_cwd).resolve()
            if sandbox_cwd is not None
            else (Path(env_sandbox).resolve() if env_sandbox else None)
        )

    def _sanitize_env(self) -> dict[str, str]:
        # Minimal, deterministic environment
        env: dict[str, str] = {}
        # Provide a safe PATH to find basic utilities
        env["PATH"] = "/usr/bin:/bin:/usr/local/bin"
        # Ensure non-interactive behavior
        env["LC_ALL"] = "C"
        # Inject explicitly provided safe variables
        for key, value in self.extra_env.items():
            env[key] = value
        return env

    def _assert_allowed(self, command: str) -> None:
        tokens = shlex.split(command)
        if not tokens:
            raise ValueError("Empty command")
        cmd = os.path.basename(tokens[0])
        if cmd not in self.allowed_commands:
            raise PermissionError(f"Command '{cmd}' is not allowed")

    def _assert_not_denied(self, command: str) -> None:
        if not self.deny_command_prefixes:
            return
        tokens = shlex.split(command)
        if not tokens:
            raise ValueError("Empty command")
        raw = tokens[0]
        cmd = os.path.basename(raw)
        for prefix in self.deny_command_prefixes:
            if cmd.startswith(prefix) or raw.startswith(prefix):
                raise PermissionError(f"Command '{cmd}' is denied by policy")

    def _resolve_working_dir(self, cwd: str | None) -> str | None:
        # No sandbox configured → pass-through or None
        if self._sandbox_root is None:
            return None if cwd is None else str(Path(cwd))

        sandbox_root = self._sandbox_root
        assert sandbox_root is not None

        # Default to sandbox root when cwd is empty
        if cwd is None or not str(cwd).strip():
            return str(sandbox_root)

        given = Path(cwd)
        candidate = given.resolve() if given.is_absolute() else (sandbox_root / given).resolve()
        # Ensure candidate is under sandbox_root
        try:
            _ = candidate.relative_to(sandbox_root)
        except Exception:
            raise PermissionError("Working directory escapes sandbox root")
        return str(candidate)

    @staticmethod
    def _combine_streams(stdout: str | None, stderr: str | None) -> str:
        if not stdout and not stderr:
            return ""
        if not stderr:
            return stdout or ""
        if not stdout:
            return stderr or ""
        if stdout.endswith("\n"):
            return f"{stdout}{stderr}"
        return f"{stdout}\n{stderr}"

    def _execute_command(
        self, argv: list[str], working_dir: str | None, env: dict[str, str]
    ) -> tuple[str, str, int, bool, int]:
        start = time.monotonic()
        proc = Popen(
            argv,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
            cwd=working_dir,
            env=env,
            text=True,
            start_new_session=True,
        )
        timeout = False
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
        except TimeoutExpired:
            timeout = True
            try:
                # Terminate whole process group
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            stdout, stderr = proc.communicate()

        duration_ms = int((time.monotonic() - start) * 1000)
        return stdout or "", stderr or "", proc.returncode, timeout, duration_ms

    @staticmethod
    def _redact_output(text: str, patterns: Iterable[re.Pattern[str]] | None = None) -> str:
        # Built-in conservative patterns for common secrets/tokens.
        # Keep false-positive risk low; broader/optional redaction happens in the function layer.
        secret_patterns: list[re.Pattern[str]] = [
            # OpenAI-style keys
            re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
            # JWTs (three base64url segments)
            re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
            # Authorization bearer values
            re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}"),
            # Long hex tokens
            re.compile(r"\b[0-9a-fA-F]{32,}\b"),
        ]
        for pat in patterns or secret_patterns:
            text = pat.sub("[REDACTED]", text)
        return text

    def run(self, command: str, cwd: str | None = None) -> dict[str, Any]:
        self._assert_not_denied(command)
        self._assert_allowed(command)

        working_dir = self._resolve_working_dir(cwd)

        env = self._sanitize_env()

        # Build argv for Popen without invoking a shell
        argv = shlex.split(command)

        stdout, stderr, exit_code, did_timeout, duration_ms = self._execute_command(
            argv, working_dir, env
        )

        combined = self._combine_streams(stdout, stderr)
        # Redact sensitive tokens prior to truncation
        redacted = self._redact_output(combined)
        out_text, was_truncated = _truncate_to_bytes(redacted, self.output_cap_bytes)

        result: dict[str, Any] = {
            "ok": (exit_code == 0) and not did_timeout,
            "exit_code": exit_code,
            "timeout": did_timeout,
            "stdout": out_text,
            "truncated": was_truncated,
            "duration_ms": duration_ms,
        }
        return result


__all__ = ["TerminalTool"]
