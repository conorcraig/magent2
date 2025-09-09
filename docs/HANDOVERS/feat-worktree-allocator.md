# Handover: Git worktree allocator for per‑agent branches

Owner: next agent
Tracking issue: https://github.com/conorcraig/magent2/issues/87

## Context

- PRD requires per‑agent git worktrees and explicit branch naming to reduce merge conflicts and map file scopes to isolated working directories.
- No worktree manager exists in the codebase.

## Deliverables

- Implement a minimal worktree manager:
  - `magent2/worktree/manager.py` with functions:
    - `create_worktree(base_dir: str, branch: str, path: str) -> None`
    - `cleanup_worktree(path: str) -> None`
    - `preflight_conflicts(allowed_paths: list[str]) -> list[str]` (stub for now; returns conflicting paths if any)
- Provide safe shell execution via Python subprocess without relying on arbitrary shell commands; enforce cwd constraints.
- Document branch naming convention: `wt/<agent>/<conversation_id[:8]>/<timestamp>`.

## File references

- New: `magent2/worktree/manager.py`
- Tests: `tests/test_worktree_manager.py`

## Design

- Shell out to `git` with explicit args; capture errors and return concise messages.
- Assume repository root as `base_dir` (can be discovered via `git rev-parse --show-toplevel` in tests) but take it as a parameter to keep the module testable.
- Keep the allocator independent of Worker/Runner wiring.

## Tests

- Skip tests if `git` is unavailable.
- In a temp repo, create a branch and worktree, create a dummy file, and cleanup.
- Preflight conflicts: construct two overlapping scopes and assert detection (string match sufficient for first version).

## Acceptance criteria

- Worktrees are created and cleaned up without affecting the main checkout.
- Tests pass locally; `just check` green.

## Risks & mitigations

- Platform differences: use portable subprocess invocations.
- Cleanup on failure: ensure cleanup is idempotent and won’t remove unrelated dirs.

## Branch and ownership

- Branch name: `feat/worktree-allocator`
- Ownership: new `magent2/worktree/*` module and tests only.