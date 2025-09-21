# Research: TUI agents + graph panes

## Prompt / Issue
- Issue: #130 — "Feature: TUI agents pane + graph pane" (milestone M2: TUI polish)
- Goal: Extend the Rust TUI (`chat_tui/`) with optional panes that surface `/agents` and `/graph/{conversation_id}` observer data.

## Current Understanding
- Rust TUI already supports multi-session chat, conversations panel, SSE streaming with resume, markdown rendering, and gateway health checks.
- Observer index + gateway endpoints are live (Redis-backed, best-effort). Shapes documented in `docs/FRONTEND_TUI.md`.
- Keybindings currently include `c` (conversations panel), `Tab`, `F2`, scrolling, etc. No existing panes for agents/graph.
- Networking uses `reqwest` async client within tokio runtime.

## Constraints / Guardrails
- Follow AGENTS.md: minimal diffs, use context engineering, run `just check` before finishing.
- Maintain responsive UI; avoid blocking render loop.
- Handle disabled observer index (endpoints return empty collections) gracefully.
- Work within current Rust crate patterns (single file for now, but consider helper modules if complexity grows).
- No additional dependencies unless justified (cargo fmt/clippy must stay green).

## References to Review
- `chat_tui/src/main.rs` (existing layout, state structs, key handling, SSE logic).
- `docs/FRONTEND_TUI.md` (API shapes, keybinding expectations, troubleshooting notes).
- `magent2/gateway/app.py` for endpoint behavior and limits.
- Observer index implementation (`magent2/observability/index.py`) to confirm edge cases (limits, empty responses).

## Open Questions / Follow-ups
- How to layout panes without overwhelming existing UI? (Split vertical/horizontal? Toggle keys `a`, `g` per issue guidance.)
- Polling cadence for `/agents` (issue suggests 2–5s). Need to decide on implementation (background task vs tick in main loop).
- Graph rendering approach: simple ASCII adjacency vs minimal summary? Need to define heuristics for >100 edges as per scope.
- Error handling: where to surface failures (status line vs pane-level message).
- Testing strategy: add Rust unit tests? Possibly factor fetch/render helpers to allow deterministic tests without full UI integration.

## Next Steps
- Confirm desired UX details with stakeholder (if any questions remain).
- Draft implementation plan (separate `plan.md`) detailing state changes, background tasks, rendering updates, and validation steps.
