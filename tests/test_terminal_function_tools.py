from __future__ import annotations

from pathlib import Path

import pytest

from magent2.tools.terminal.function_tools import terminal_run


@pytest.fixture(autouse=True)
def _reset_terminal_policy_cache() -> None:
    # Ensure each test starts with a fresh terminal policy snapshot
    import magent2.tools.terminal.function_tools as ft

    ft._reset_terminal_policy_cache_for_tests()


@pytest.fixture()
def tmp_script_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


def _setenv(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_disallowed_command_returns_failure_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="echo")
    out = terminal_run("bash -lc 'echo hi'")
    assert out.startswith("ok=false ")
    assert "error:\n" in out


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
    # Use placeholders that should not trigger secret scanners but still match our regex
    redaction_token_value = "sk-PLACEHOLDER12345"
    redact_substring_value = "REDACTION_TEST_VALUE"
    _setenv(
        monkeypatch,
        TERMINAL_ALLOWED_COMMANDS="bash",
        TERMINAL_REDACT_SUBSTRINGS=redact_substring_value,
    )
    cmd = f"bash -lc 'echo {redaction_token_value}; echo {redact_substring_value}'"
    out = terminal_run(cmd)
    # Built-in pattern masks sk- tokens and api_key labels
    assert "[REDACTED]" in out
    assert redaction_token_value not in out
    assert redact_substring_value not in out


def test_redaction_jwt_and_bearer_in_function_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="bash")
    # Low-entropy placeholder JWT-like and Bearer strings
    jwt = (
        "AAAAAAAAaaaaaaaa0000----____."  # header
        "BBBBBBBBbbbbbbbb1111----____."  # payload
        "CCCCCCCCcccccccc2222----____"  # signature
    )
    bearer = "Bearer AAAAAAAA.bbbbbbbb-0000____"  # pragma: allowlist secret
    out = terminal_run(f"bash -lc 'echo {jwt}; echo {bearer}'")
    assert "[REDACTED]" in out
    assert jwt not in out
    assert bearer not in out


def test_high_entropy_token_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="bash")
    high_entropy = (  # pragma: allowlist secret
        "A1b2C3d4E5f6G7h8I9j0K1L2M3N4O5P6Q7R8S9T0UVWXyz_+==/"  # pragma: allowlist secret
        "A1b2C3d4E5f6G7h8I9j0"  # pragma: allowlist secret
    )
    out = terminal_run(f"bash -lc 'echo {high_entropy}'")
    assert "[REDACTED]" in out
    assert high_entropy not in out


def test_returns_failure_string_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly deny all commands -> PermissionError -> function should return failure string
    _setenv(monkeypatch, TERMINAL_ALLOWED_COMMANDS="")
    out = terminal_run("echo hello")
    assert out.startswith("ok=false ")
    assert "error:\n" in out


def test_defaults_allow_known_command_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When TERMINAL_ALLOWED_COMMANDS is unset, built-in defaults should apply."""
    # Ensure variable is absent
    monkeypatch.delenv("TERMINAL_ALLOWED_COMMANDS", raising=False)
    # Reload module to clear any cached policy
    import importlib

    import magent2.tools.terminal.function_tools as ft

    importlib.reload(ft)

    out = ft.terminal_run("echo ok")
    assert out.startswith("ok=true ")
    assert "output:\nok" in out


def test_empty_string_denies_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly empty allowlist should deny all commands."""
    monkeypatch.setenv("TERMINAL_ALLOWED_COMMANDS", "")
    # Reload to clear cached policy
    import importlib

    import magent2.tools.terminal.function_tools as ft

    importlib.reload(ft)

    out = ft.terminal_run("echo should_fail")
    assert out.startswith("ok=false ")
    assert "error:\n" in out
