from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from magent2.team.worktree import WorktreeError, allocate_worktree


def _run(cmd: list[str], cwd: str) -> int:
    proc = subprocess.run(
        cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return proc.returncode


@pytest.mark.docker
def test_allocate_worktree_creates_branch_and_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _run(["git", "init"], str(repo)) == 0
    # Initial commit required before creating worktrees
    (repo / "file.txt").write_text("hi")
    assert _run(["git", "add", "."], str(repo)) == 0
    assert _run(["git", "commit", "-m", "init"], str(repo)) == 0

    wt = allocate_worktree(repo_root=str(repo), agent_name="BotA", ticket="ISSUE-70")
    assert Path(wt.path).exists()
    assert wt.branch.startswith("feature/ISSUE-70/")


def test_allocate_worktree_raises_outside_git(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError):
        allocate_worktree(repo_root=str(tmp_path), agent_name="BotB")

