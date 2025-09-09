# Handover: Gateway SSE non‑blocking offload (to_thread + yield cadence)

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/80>

## Context

- `magent2/gateway/app.py` implements `/stream/{conversation_id}` as an async generator, but it synchronously calls `bus.read(...)` in the event loop and adds a fixed sleep. Under load, this can increase latency or risk loop stalls.

## Deliverables

- Offload blocking/synchronous bus reads using `await asyncio.to_thread(bus.read, topic, last_id=..., limit=...)`.
- Emit events as soon as they arrive; avoid sleeping immediately after a yield.
- Keep a small sleep only when no items are returned to prevent tight loops.
- Preserve response format and `max_events` semantics; do not change event payload shapes.

## File references

- `magent2/gateway/app.py`
- `tests/test_gateway.py`

## Design

- Maintain local variables `last_id`, `sent`. Replace the synchronous `items = list(bus.read(...))` with the offloaded call.
- Only `await asyncio.sleep(...)` when `items` is empty.
- Keep current behavior that passes through JSON payloads and filters as implemented.

## Tests

- Existing gateway tests should remain green.
- Optionally add a timing‑insensitive test that publishes two events and asserts both are delivered without extra delay (don’t assert timing, only ordering and count).

## Acceptance criteria

- `/stream/{conversation_id}` remains responsive under load.
- No event‑loop stalls; existing tests pass.
- `just check` passes locally.

## Risks & mitigations

- If the bus adapter becomes async later, this remains valid; `to_thread` is a minimal, low‑risk bridge.
- Ensure the generator returns promptly when `max_events` is reached.

## Branch and ownership

- Branch name: `feat/gateway-sse-offload`
- Ownership: restrict edits to `magent2/gateway/app.py` to avoid conflicts with other streams or tools.
