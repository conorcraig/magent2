# Contributing to magent2

This repo is designed for parallel development by multiple agents. Please follow these rules to avoid conflicts.

## Branches and issues

- Create feature branches per issue: `feat/<slug>-#<issue>` (e.g. `feat/bus-envelope-#3`).
- Reference the issue number in every commit message: `chore: #3 add Bus interface`.
- One logical component per branch (e.g., `magent2/tools/terminal/`). Do not edit unrelated areas.

## Contracts (frozen)

- Message envelope and stream events v1: see `docs/CONTRACTS.md`.
- Bus API v1: see `docs/CONTRACTS.md`.
- If you need changes, open a new issue proposing `v1.1` with exact field/API diffs.

## Tests (TDD)

- Add tests first under `tests/` for your component.
- Keep tests fast and deterministic; mock network/processes where possible.
- Use `uv run pytest` locally. CI is optional for now.

## Linting & types

- Use pre-commit. Only run on staged files.
- `uv run ruff check` and `uv run ruff format` should be clean.
- `uv run mypy` should be clean for changed files.

## Directory ownership

- `magent2/models/` – shared schemas (do not break v1).
- `magent2/bus/` – bus interface/adapters (no changes to interface without approval).
- `magent2/worker/` – subscriber loop and runner wiring.
- `magent2/tools/*` – independent tools. Avoid cross-tool imports.
- `magent2/gateway/` – optional HTTP+SSE.
- `magent2/observability/` – tracing/logs/metrics.

## Secrets

- Never print or commit secrets. `.env.example` lists required vars.
