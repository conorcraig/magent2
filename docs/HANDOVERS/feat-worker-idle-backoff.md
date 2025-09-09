# Handover: Worker idle CPU reduction (blocking reads and backoff)

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/86>

## Context

- Current `Worker` loop in `magent2/worker/__main__.py` spins: `while True: worker.process_available()` with no delay when no messages are processed.
- `RedisBus` consumer-group path (`_read_with_group`) uses `block=0` (non‑blocking) on `xreadgroup`, also contributing to tight loops when polled.
- We want near‑zero idle CPU without changing public contracts or event shapes.

## Deliverables

- Introduce optional blocking for Redis Streams group reads:
  - Add `block_ms: int | None = None` parameter to `magent2/bus/redis_adapter.RedisBus`.
  - When set and a consumer group is configured, pass it as `block=block_ms` to `xreadgroup`.
- Update `magent2/worker/__main__.py` to construct a group consumer with a reasonable default (e.g., `group_name="magent2"`, `consumer_name=<uuid>`, `block_ms≈1000`).
- Retain an Option A fallback: if group is not set, add a small exponential backoff sleep in the main loop when `process_available()` returns 0 (start ~50ms, cap ~200ms).
- No behavior changes for message processing or event payloads.

## File references

- `magent2/bus/redis_adapter.py`
- `magent2/worker/__main__.py`
- Tests (no new tests required, but see below for optional unit coverage)

## Design notes

- The `block_ms` parameter only affects the code path when a consumer group is configured; tail reads remain unchanged.
- A small backoff in `__main__` protects non‑group setups from tight loops.
- At‑least‑once semantics are preserved; ack behavior remains unchanged.

## Testing plan (offline)

- Add a lightweight unit test for `RedisBus` group mode argumenting (optional):
  - Inject a fake redis client capturing calls to `xreadgroup` and assert `block=<block_ms>` is passed when `block_ms` is not `None`.
- Ensure existing tests pass unchanged (`uv run pytest -q`).

## Acceptance criteria

- Idle `Worker` process shows near‑zero CPU when there is no traffic (manual smoke acceptable).
- No regressions in message processing order or delivery.
- All tests and quality gates pass (`just check`).

## Validation steps

1) Local smoke (with Redis running or using the provided docker compose):
   - Start gateway and worker via compose.
   - Observe worker CPU at idle (top/htop): should be near zero.
2) Send a message via `scripts/client.py` and confirm behavior unchanged (tokens/output streamed as before).

## Risks & mitigations

- Redis connectivity variations: if `xreadgroup` blocking behaves unexpectedly in certain Redis versions, the Option A backoff provides a safe fallback.
- Starvation: use a reasonable `block_ms` (≈1s) to keep perceived latency low while avoiding busy waits.

## Branch and ownership

- Branch name: `feat/worker-idle-backoff`
- Ownership: restricted to `magent2/bus/redis_adapter.py` and `magent2/worker/__main__.py` to minimize merge conflicts.
