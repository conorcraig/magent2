from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: str | None = None, timeout: float = 5.0) -> tuple[int, str, str]:
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except Exception:
        proc.kill()
        out, err = proc.communicate()
    return proc.returncode, out, err


def _ensure_git_repo(root: Path) -> None:
    code, _, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root))
    if code != 0:
        raise WorktreeError(f"not a git repository: {root}")


def _sanitize_branch_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._/-]+", "-", name.strip())
    s = re.sub(r"/+", "/", s)
    return s.strip("/") or "agent"


@dataclass(slots=True)
class Worktree:
    path: str
    branch: str


def allocate_worktree(*, repo_root: str, agent_name: str, ticket: str | None = None) -> Worktree:
    """Create a per-agent git worktree on a new branch.

    Branch convention: feature/{ticket or 'task'}/{agent_name}
    Worktree directory: .worktrees/{agent_name}
    """
    root = Path(repo_root).resolve()
    _ensure_git_repo(root)

    ticket_part = _sanitize_branch_name(ticket or "task")
    agent_part = _sanitize_branch_name(agent_name)
    branch = f"feature/{ticket_part}/{agent_part}"
    worktrees_dir = root / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_dir / agent_part

    # Create branch if missing
    code, _, _ = _run(["git", "rev-parse", "--verify", branch], cwd=str(root))
    if code != 0:
        code, _, err = _run(["git", "checkout", "-b", branch], cwd=str(root))
        if code != 0:
            raise WorktreeError(f"failed to create branch {branch}: {err.strip()}")

    # Add worktree (idempotent if already exists)
    if not wt_path.exists():
        code, _, err = _run(["git", "worktree", "add", str(wt_path), branch], cwd=str(root))
        if code != 0:
            # If worktree already exists for this path, continue
            if "already exists" not in err.lower():
                raise WorktreeError(f"failed to add worktree: {err.strip()}")

    return Worktree(path=str(wt_path), branch=branch)


__all__ = ["Worktree", "WorktreeError", "allocate_worktree"]

