from __future__ import annotations

from pathlib import Path

import pytest

from magent2.tools.terminal.tool import TerminalTool


def test_denylist_blocks_even_if_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMINAL_DENY_COMMANDS", "rm")
    tool = TerminalTool(allowed_commands=["rm", "echo"])  # allowed but should still be denied
    with pytest.raises(PermissionError):
        tool.run("rm -rf /tmp")


def test_sandbox_cwd_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path / "sb"
    outside = tmp_path / "outside"
    sandbox.mkdir()
    outside.mkdir()
    (sandbox / "file.txt").write_text("hi")
    monkeypatch.setenv("TERMINAL_SANDBOX_CWD", str(sandbox))
    tool = TerminalTool(allowed_commands=["bash", "cat", "echo"])

    # Default cwd should be sandbox root
    result = tool.run("bash -lc 'pwd' ")
    assert result["ok"] is True
    assert str(sandbox) in result["stdout"].strip()

    # Relative path within sandbox OK
    result2 = tool.run("bash -lc 'test -f file.txt && echo ok'", cwd=".")
    assert result2["ok"] is True
    assert "ok" in result2["stdout"]

    # Escaping attempts should be blocked
    with pytest.raises(PermissionError):
        tool.run("bash -lc 'pwd'", cwd="../outside")

    # Absolute path outside sandbox should be blocked
    with pytest.raises(PermissionError):
        tool.run("bash -lc 'pwd'", cwd=str(outside))


def test_redaction_of_secrets(tmp_path: Path) -> None:
    tool = TerminalTool(allowed_commands=["bash"])
    fake_secret = "sk-abc1234567890"  # pragma: allowlist secret
    result = tool.run(f"bash -lc 'echo {fake_secret} && echo {fake_secret} 1>&2'")
    assert result["ok"] is True
    assert fake_secret not in result["stdout"]
    assert "[REDACTED]" in result["stdout"]


def test_redaction_of_jwt_and_bearer(tmp_path: Path) -> None:
    tool = TerminalTool(allowed_commands=["bash"])
    # Minimal-looking JWT-like (3 segments), values are placeholders
    # Low-entropy placeholder JWT-like string (3 segments)
    jwt = (
        "AAAAAAAAaaaaaaaa0000----____."  # header
        "BBBBBBBBbbbbbbbb1111----____."  # payload
        "CCCCCCCCcccccccc2222----____"  # signature
    )
    # Low-entropy placeholder Bearer token
    bearer = "Bearer AAAAAAAA.bbbbbbbb-0000____"  # pragma: allowlist secret
    cmd = f"bash -lc 'echo {jwt}; echo {bearer}'"
    result = tool.run(cmd)
    assert result["ok"] is True
    assert "[REDACTED]" in result["stdout"]
    assert jwt not in result["stdout"]
    assert bearer not in result["stdout"]
