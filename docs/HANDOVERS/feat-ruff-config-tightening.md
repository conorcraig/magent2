# Handover: Ruff configuration tightening and fixes (stage 1)

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/82>

## Context

- `pyproject.toml` currently selects a minimal Ruff rule set (E, F, I, UP).
- We want to enable low‑churn rules for bugbear/security/no‑print in lib code, then fix violations locally without adding inline ignores.

## Deliverables

- Update `[tool.ruff.lint].select` to include `"B","S","T20"`.
- Scope rules to exclude `scripts/` and `tests/` from T20 (prints allowed there) via per‑dir configuration.
- Fix violations across library code without weakening rules.

## File references

- `pyproject.toml`
- Source tree under `magent2/`
- Tests unchanged

## Design notes

- Prefer small, targeted edits; replace prints with logger usage in library code if any.
- Do not add ignore comments; refactor code to satisfy rules.

## Acceptance criteria

- `uv run ruff check` clean for project.
- `just check` passes.

## Risks & mitigations

- Churn across many files; keep PR focused and small per violation group.

## Branch and ownership

- Branch name: `chore/ruff-tighten-stage-1`
- Ownership: `pyproject.toml` and targeted library code fixes.
