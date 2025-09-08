# Handover: Client displays `log` events and docs one-shot usage (#59)

Owner: next agent picking up Issue #59

## Context

- Goal: Enhance the terminal client to surface agent log lines alongside tokens/tool steps, and document one‑shot usage.
- Current client: `scripts/client.py` already parses `log` events in `StreamPrinter._handle_log` and supports one‑shot mode hooks.
- References: `docs/refs/sse.md`, `docs/refs/quality-gates.md`.

## Deliverables

- Stream handling: ensure `event: "log"` is rendered as `[log][LEVEL] component?: message` with level filtering.
- Docs: add README example for one‑shot mode, e.g. `uv run python scripts/client.py --message "..." --conv conv-123` and `--base-url auto`.
- Ensure non-interactive one‑shot mode times out cleanly and exits with explicit code.

## Implementation notes

- Verify `StreamPrinter._handle_log` level mapping and threshold; default `log_level` in `ClientConfig` is `info`.
- Confirm `main` path for `--message` and ensure README example matches actual flags (`--base-url`, `--conv`, `--agent`, `--sender`, `--timeout`).

## Tests (offline)

- Add `tests/test_client_log_events.py`:
  - Monkeypatch `httpx.stream` to yield a `log` event followed by an `output` event.
  - Assert pretty‑printed output includes `[log][INFO]` (or mapped level) and optional component prefix.
  - Confirm one‑shot exit code is 0.

## Docs updates

- `README.md`:
  - Add a short section "Non‑interactive one‑shot mode" with exact command examples and notes on `--base-url auto` discovery.
  - Mention log rendering and `--log-level` behavior if exposed via CLI in the future.

## Validation

- `uv run pytest -q tests/test_client_log_events.py`
- `just check`
