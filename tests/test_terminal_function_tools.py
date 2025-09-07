from __future__ import annotations

import os
from pathlib import Path

import pytest

from magent2.tools.terminal.function_tools import terminal_run


@pytest.fixture()
def tmp_script_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


def _setenv(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_blocks_disallowed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="echo")
    with pytest.raises(PermissionError):
        terminal_run("bash -lc 'echo hi'")


def test_allows_allowed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="echo")
    out = terminal_run("echo hello")
    assert out.startswith("ok=true ")
    assert "output:\nhello" in out


def test_timeout_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(
        monkeypatch,
        TERMINAL_ALLOWED_COMMANDS="bash,sleep",
        TERMINAL_TIMEOUT_SECONDS="0.5",
    )
    out = terminal_run("bash -lc 'sleep 5'")
    assert "ok=false" in out
    assert "timeout=true" in out


def test_output_truncation_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure both byte-cap (tool) and char-cap (function) are exercised
    _setenv(
        monkeypatch,
        TERMINAL_ALLOWED_COMMANDS="python3",
        TERMINAL_OUTPUT_CAP_BYTES="64",
        TERMINAL_FUNCTION_OUTPUT_MAX_CHARS="40",
    )
    out = terminal_run("python3 -c \"print('x'*1000)\"")
    # function layer cap
    assert len(out.split("output:\n", 1)[1]) <= 40
    # status should indicate truncated by the tool layer
    assert "truncated=true" in out


def test_redaction_via_env_and_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "sk-ABCDEF0123456789"
    secret = "TOPSECRET"
    _setenv(
        monkeypatch,
        TERMINAL_ALLOWED_COMMANDS="bash",
        TERMINAL_REDACT_SUBSTRINGS=secret,
    )
    cmd = f"bash -lc 'echo api_key: {token}; echo {secret}'"
    out = terminal_run(cmd)
    # Built-in pattern masks sk- tokens and api_key labels
    assert "[REDACTED]" in out
    assert token not in out
    assert secret not in out

