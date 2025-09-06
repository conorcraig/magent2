# Parallel work guidelines

This repo is optimized for multiple agents working in parallel without direct communication.

## What is frozen

- Message envelope and stream events v1 – see `docs/CONTRACTS.md` (source: `magent2/models/envelope.py`).
- Bus API v1 – see `docs/CONTRACTS.md` (source: `magent2/bus/interface.py`).

## Branching and ownership

- One component per branch. Suggested paths:
  - `magent2/bus/redis_adapter.py` (#3)
  - `magent2/worker/` (#4)
  - `magent2/tools/terminal/` (#5)
  - `magent2/tools/todo/` (#6)
  - `magent2/tools/mcp/` (#7)
  - `magent2/gateway/` (#8)
  - `magent2/observability/` (#9)
- Branch names: `feat/<slug>-#<issue>` (e.g., `feat/worker-streaming-#4`).
- Every commit must reference the issue number (e.g., `feat: #4 add worker loop`).

## TDD expectations

- Add tests first in `tests/` near your domain.
- Keep tests fast; mock subprocess/network as needed.
- Use `uv run pytest` locally. Only stage affected files for pre-commit.

## Don’ts

- Don’t change frozen contracts in v1.
- Don’t touch directories outside your component.
- Don’t print or commit secrets.

## Handoffs

- Worker (#4) depends on contracts (#3) – safe to start now.
- Gateway (#8) depends on contracts – safe to start.
- Tools (#5/#6/#7) are independent (Chat tool will later use Bus).
- Observability (#9) can wire in incrementally.
