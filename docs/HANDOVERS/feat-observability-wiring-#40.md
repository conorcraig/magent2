# Handover: Observability wiring across Worker/Runner/Tools (#40)

Owner: next agent picking up Issue #40

## Context

- Goal: Propagate `run_id`/`conversation_id` consistently across Worker/Runner/Tools; emit JSON logs; add counters in key paths.
- Partial state: Observability docs and some helpers exist (`docs/refs/observability.md`, tests in `tests/test_observability.py`).
- Scope: Internal plumbing only; do not change contract event shapes.

## Deliverables

- Add correlation fields to logs/events emitted inside Worker run loop and tool wrappers.
- Counters for: runs started/completed/errored; tool calls; retries.
- Tests asserting presence of correlation fields and counter increments.

## Design sketch

- Logging:
  - Centralize a module logger (e.g., `logging.getLogger("magent2")`) configured with a JSON handler in tests.
  - When Worker starts processing an envelope, assign a `run_id` (uuid) and pass it through to runner and tools.
  - Use `logger.info(..., extra={"run_id": run_id, "conversation_id": env.conversation_id})`.

- Counters:
  - Local in‑process counters via a lightweight registry (dict of str→int) with increment helpers.
  - Expose read/reset functions for tests.

## Tests (offline)

- Add `tests/test_observability_wiring.py`:
  - Configure logger with a capturing handler; run a minimal fake runner stream emitting a token + output; assert logs include both IDs.
  - Call a dummy tool wrapper and assert counters/logs carry IDs.

## File references

- `magent2/worker/worker.py` (run loop)
- Any tool wrappers under `magent2/tools/...`
- `tests/test_observability.py` (existing patterns)

## Validation

- `uv run pytest -q tests/test_observability_wiring.py`
- `just check`

## Risks

- Logging handler conflicts; keep handler configuration local to tests.
- Over‑logging; keep key fields only.
