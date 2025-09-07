from __future__ import annotations

from pathlib import Path

import pytest

from magent2.tools.terminal.tool import TerminalTool


@pytest.fixture()
def tmp_script_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


def write_script(dirpath: Path, name: str, content: str) -> Path:
    path = dirpath / name
    path.write_text(content)
    path.chmod(0o755)
    return path


def test_allowlist_blocks_unapproved_command(tmp_script_dir: Path) -> None:
    tool = TerminalTool(allowed_commands=["echo", "bash", "python3"])  # whitelist
    # a clearly unsafe command name not in allowlist
    cmd = "rm -rf /"
    with pytest.raises(Exception):
        tool.run(cmd)


def test_allowlist_allows_whitelisted_command(tmp_script_dir: Path) -> None:
    tool = TerminalTool(allowed_commands=["echo", "bash", "python3"])
    result = tool.run("echo hello")
    assert result["ok"] is True
    assert "hello" in result["stdout"]


def test_timeout_kills_long_running_process(tmp_script_dir: Path) -> None:
    tool = TerminalTool(allowed_commands=["bash", "sleep"], timeout_seconds=0.5)
    # sleep longer than timeout
    result = tool.run("bash -lc 'sleep 5'")
    assert result["ok"] is False
    assert result["timeout"] is True


def test_output_cap_truncates_large_output(tmp_script_dir: Path) -> None:
    tool = TerminalTool(allowed_commands=["python3"], output_cap_bytes=100)
    # generate 1000 bytes via python
    result = tool.run("python3 -c \"print('x'*1000)\"")
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["stdout"].encode()) <= 100


def test_non_interactive_prevents_stdin_block(tmp_script_dir: Path) -> None:
    tool = TerminalTool(allowed_commands=["bash"], timeout_seconds=1.0)
    # command that would wait for input; we ensure no stdin is attached and it times out or exits
    result = tool.run("bash -lc 'read -t 0.1 var || echo noinput'")
    assert result["ok"] is True
    assert "noinput" in result["stdout"]


def test_sanitized_env(tmp_script_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "supersecret")
    tool = TerminalTool(allowed_commands=["bash"], extra_env={"SAFE_FLAG": "1"})
    result = tool.run("bash -lc 'env | sort'", cwd=str(tmp_script_dir))
    # environment should not include SECRET_TOKEN
    assert "SECRET_TOKEN=" not in result["stdout"]
    # but should include SAFE_FLAG
    assert "SAFE_FLAG=1" in result["stdout"]
