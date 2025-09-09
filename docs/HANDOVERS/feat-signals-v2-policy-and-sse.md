# Handover: Signals v2 — wait_any/all, policy, and SSE visibility

Owner: next agent

## Context

- Signals exist: `magent2/tools/signals/impl.py` implements `send_signal` and `wait_for_signal` and has decorated wrappers.
- PRD requires richer coordination: multi‑wait (`wait_any`, `wait_all`), topic policy (allowlist/prefix), payload caps/redaction, and visibility via SSE (`signal_send`, `signal_recv` events).

## Deliverables

1) Multi‑wait APIs in implementation and wrappers:
   - `wait_for_any(topics: list[str], last_ids: dict[str,str] | None, timeout_ms: int)`
   - `wait_for_all(topics: list[str], last_ids: dict[str,str] | None, timeout_ms: int)`
   - Decorated function tools `signal_wait_any`, `signal_wait_all`.
2) Policy & safety:
   - Topic namespace allowlist via env (e.g., `SIGNAL_TOPIC_PREFIX=signal:<team>/`). Deny topics outside the prefix.
   - Payload size cap (bytes) via env (e.g., `SIGNAL_PAYLOAD_MAX_BYTES`, default small and safe). Truncate or reject with actionable error.
   - Redact sensitive keys using `magent2.observability.SENSITIVE_KEYS` before returning payloads.
3) SSE visibility (optional but preferred):
   - When `send_signal` succeeds, if a `conversation_id` is available (explicit param or via run context), also publish a small event on `stream:{conversation_id}` with `{event: "signal_send", topic, message_id}`.
   - When `wait_*` returns a message, similarly publish `{event: "signal_recv", topic, message_id, payload_len}` to `stream:{conversation_id}` if available.
4) Reliability:
   - Persist and restore `last_id` cursors in session/run context for long waits and reliability across restarts. Provide helpers to get/set per‑topic cursors keyed by `conversation_id`.

## File references

- `magent2/tools/signals/impl.py` (core)
- `magent2/tools/signals/wrappers.py` (decorated tools)
- `magent2/observability/__init__.py` (SENSITIVE_KEYS reference)
- `tests/test_signals.py` (+ new tests)

## Design notes

- Keep implementation sync and bus‑agnostic.
- For multi‑wait, perform short polling across topics with a deadline; return the first seen (any) or all seen (all). Use small sleeps (e.g., 25–50ms) between polls.
- Policy helpers:
  - `_require_allowed_topic(topic: str) -> None` using `SIGNAL_TOPIC_PREFIX` (optional; if unset, default allow).
  - `_cap_and_redact(payload: dict) -> dict` (truncate JSON string if over cap; redact sensitive keys).
- Run context (`get_run_context()`) can provide `conversation_id` for SSE publish convenience.

## Tests (TDD)

- Extend `tests/test_signals.py`:
  - `wait_for_any` returns the first of multiple topics; respects per‑topic last ids.
  - `wait_for_all` returns after each topic has a new message (or timeout path).
  - Policy prefix denial raises `ValueError`.
  - Payload cap truncates or rejects with a clear error.
  - SSE visibility: inject in‑memory bus and assert a `signal_send` and `signal_recv` event appears on `stream:{conversation_id}` when a `conversation_id` is provided (via explicit param or context).

## Acceptance criteria

- Unit tests cover multi‑wait paths, policy enforcement, and SSE visibility.
- Existing tests remain green.
- `just check` passes.

## Risks & mitigations

- Publishing to stream without a known `conversation_id` is skipped (no error).
- Keep payload caps conservative to avoid large event bodies.

## Branch and ownership

- Branch name: `feat/signals-v2`
- Ownership: `magent2/tools/signals/*` and tests only. Avoid edits to `gateway`/`worker` to reduce conflict risk.
