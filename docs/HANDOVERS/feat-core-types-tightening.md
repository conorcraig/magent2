# Handover: Tighten core types for BusMessage.payload

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/78>

## Context

- `magent2/bus/interface.py` defines `BusMessage.payload: dict`. We want explicit typing `dict[str, Any]` for better static safety.
- This is a low‑risk internal change but may require minor callsite/type adjustments.

## Deliverables

- Change `BusMessage.payload: dict` → `dict[str, Any]` in `magent2/bus/interface.py`.
- Update affected imports/type hints where necessary; do not change runtime behavior.
- Ensure mypy and tests are green for changed files.

## File references

- `magent2/bus/interface.py`
- Callers across the codebase (search for `BusMessage(` and `payload=` usages)
- Tests: full suite

## Tests

- No new tests required. Run existing tests and types.

## Acceptance criteria

- `uv run mypy` clean for changed files.
- `uv run pytest -q` green.
- No contract or runtime behavior changes.

## Risks & mitigations

- Stricter typing may surface Any‑related warnings elsewhere; limit changes to annotations and minimal casts if unavoidable.

## Branch and ownership

- Branch name: `chore/core-types-busmessage-payload`
- Ownership: `magent2/bus/interface.py` and minimal callsite annotation tweaks.
