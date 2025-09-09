# Handover: Docs alignment for SSE, Redis Streams, and Terminal policy

Owner: next agent
Tracking issue: https://github.com/conorcraig/magent2/issues/79

## Context

- Reference docs exist under `docs/refs/`. We need to align them with implemented behavior once code changes land (SSE offload, terminal redaction env vars, optional blocking reads).

## Deliverables

- Update `docs/refs/sse.md` to mention the `asyncio.to_thread` pattern and guidance on yield cadence, plus `max_events` usage.
- Update `docs/refs/redis-streams.md` to document optional consumer group blocking with `block_ms` in the bus adapter.
- Ensure README Terminal policy section covers `TERMINAL_REDACT_SUBSTRINGS` and `TERMINAL_REDACT_PATTERNS` and examples.

## File references

- `docs/refs/sse.md`
- `docs/refs/redis-streams.md`
- `README.md`

## Acceptance criteria

- Docs match current code behavior and env variables; examples run locally.

## Branch and ownership

- Branch name: `docs/alignment-sse-redis-terminal`
- Ownership: docs only.