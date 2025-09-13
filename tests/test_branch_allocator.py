from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from magent2.team.branch import BranchError, allocate_branch


def _run(cmd: list[str], cwd: str) -> int:
    proc = subprocess.run(
        cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return proc.returncode


@pytest.mark.docker
def test_allocate_branch_creates_and_checks_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _run(["git", "init"], str(repo)) == 0
    # Configure identity for commit in isolated temp repo
    assert _run(["git", "config", "user.email", "test@example.com"], str(repo)) == 0
    assert _run(["git", "config", "user.name", "Test User"], str(repo)) == 0
    (repo / "a.txt").write_text("hi")
    assert _run(["git", "add", "."], str(repo)) == 0
    assert _run(["git", "commit", "-m", "init"], str(repo)) == 0

    br = allocate_branch(repo_root=str(repo), agent_name="BotA", ticket="ISSUE-70")
    assert br.name.startswith("feature/ISSUE-70/")
    # Verify HEAD is on the allocated branch
    head = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), text=True
    ).strip()
    assert head == br.name


def test_allocate_branch_fails_outside_git(tmp_path: Path) -> None:
    with pytest.raises(BranchError):
        allocate_branch(repo_root=str(tmp_path), agent_name="BotB")

