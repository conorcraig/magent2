from __future__ import annotations

import os
import shlex
import signal
import time
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
    ) -> None:
        self.allowed_commands: list[str] = allowed_commands or []
        self.timeout_seconds: float = timeout_seconds
        self.output_cap_bytes: int = output_cap_bytes
        self.extra_env: dict[str, str] = extra_env or {}

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

    def run(self, command: str, cwd: str | None = None) -> dict[str, Any]:
        self._assert_allowed(command)

        working_dir: str | None
        if cwd is None:
            working_dir = None
        else:
            working_dir = str(Path(cwd))

        env = self._sanitize_env()

        # Build argv for Popen without invoking a shell
        argv = shlex.split(command)

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

        combined = stdout or ""
        if stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += stderr

        out_text, was_truncated = _truncate_to_bytes(combined, self.output_cap_bytes)

        result: dict[str, Any] = {
            "ok": (proc.returncode == 0) and not timeout,
            "exit_code": proc.returncode,
            "timeout": timeout,
            "stdout": out_text,
            "truncated": was_truncated,
            "duration_ms": duration_ms,
        }
        return result


__all__ = ["TerminalTool"]
