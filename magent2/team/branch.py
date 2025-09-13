from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class BranchError(RuntimeError):
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
        raise BranchError(f"not a git repository: {root}")


def _sanitize_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._/-]+", "-", name.strip())
    s = re.sub(r"/+", "/", s)
    return s.strip("/") or "task"


@dataclass(slots=True)
class Branch:
    name: str


def allocate_branch(*, repo_root: str, agent_name: str, ticket: str | None = None) -> Branch:
    """Create or switch to a normal branch for an agent on the main worktree.

    Branch convention: feature/{ticket or 'task'}/{agent_name}
    The branch is created from the current HEAD if it does not exist.
    """
    root = Path(repo_root).resolve()
    _ensure_git_repo(root)

    ticket_part = _sanitize_name(ticket or "task")
    agent_part = _sanitize_name(agent_name)
    name = f"feature/{ticket_part}/{agent_part}"

    # If branch exists, just checkout; otherwise create it
    code, _, _ = _run(["git", "rev-parse", "--verify", name], cwd=str(root))
    if code == 0:
        code, _, err = _run(["git", "checkout", name], cwd=str(root))
        if code != 0:
            raise BranchError(f"failed to checkout {name}: {err.strip()}")
    else:
        code, _, err = _run(["git", "checkout", "-b", name], cwd=str(root))
        if code != 0:
            raise BranchError(f"failed to create branch {name}: {err.strip()}")
    return Branch(name=name)


__all__ = ["Branch", "BranchError", "allocate_branch"]

