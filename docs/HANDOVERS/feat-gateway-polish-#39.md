# Handover: Gateway polish (SSE multiplex, error mapping, request validation) (#39)

Owner: next agent picking up Issue #39

## Context

- Goal: Improve Gateway by multiplexing SSE event types explicitly, refining error mapping, adding request model validation, and light health/ready checks.
- Current: `magent2/gateway/app.py` exposes `/send` and `/stream/{conversation_id}` with basic validation and a simple polling loop; events are emitted as single JSON lines under `data:`.
- References: `docs/refs/sse.md`, `docs/CONTRACTS.md`, `tests/test_gateway.py`.

## Deliverables

- Pydantic model for `/send` request; return 422 on validation errors.
- SSE format: use explicit `event` types (already in payload) and ensure stable shapes for `token`, `tool_step`, `output`, and future `user_message`/`log`.
- Add `/ready` with a lightweight bus check (optional: guard behind a bus interface that can be faked in tests).
- Map common errors to appropriate HTTP statuses.

## Design notes

- Validation:
  - Define `SendRequest` Pydantic model with required fields: `id`, `conversation_id`, `sender`, `recipient`, `type` (=="message"), `content`.
  - FastAPI will handle 422 responses on invalid input automatically.

- SSE multiplexing:
  - Keep emitting one JSON per `data:` line; include `event` field explicitly (status quo).
  - Consider emitting an `event:` line as well if adopting browser EventSource; tests and client already parse via JSON.

- Ready check:
  - Add `/ready` that calls a minimal `bus.ping()` or performs a harmless `read` to confirm connectivity.
  - In tests, inject a fake bus with a deterministic response.

## Tests (offline)

- Extend `tests/test_gateway.py`:
  - Validation errors: invalid `/send` payload returns 422.
  - SSE: use `max_events` to capture and assert a mapped event sample.
  - Ready: `/ready` returns ok when bus mock reports healthy.

## File references

- `magent2/gateway/app.py`
- `tests/test_gateway.py`

## Validation

- `uv run pytest -q tests/test_gateway.py -q`
- `just check`

## Risks

- Backward compatibility: retain current JSON `data:` line format to avoid breaking existing client.
- Bus readiness in tests: use mock/fake to avoid Redis dependence.
