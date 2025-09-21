# Research: TUI typing/status indicator

## Prompt / Issue
- Issue: #166 — "TUI: Typing/status indicator and tool-progress spinner" (milestone M2: TUI polish).
- Goal: Provide visible feedback in the Rust TUI when an agent is processing a request or running a tool.

## Current Understanding
- Existing TUI shows streamed tokens and tool events but no immediate signal after send.
- SSE events include `tool_step` with status changes suitable for progress display.
- UI already tracks per-session state (`ChatSession` with `gen`, `stream_task`, etc.).
- Input/footer area could host a spinner/status string without major layout changes.

## Constraints / Guardrails
- Indicators must not block input or cause layout jitter; update via timer/interval.
- Must auto-clear on first output/token or on stream errors/disconnect.
- Spinner should run per session (independent across tabs) and handle long tool calls (display tool name + elapsed time).
- Handle error states gracefully (replace spinner with error message on stream failure).
- Respect AGENTS.md workflow (minimal diff, tests, `just check`).

## References to Review
- `chat_tui/src/main.rs` (state structs, event handling, render logic).
- SSE event handling logic (`handle_sse_line`) for `tool_step` start/finish events.
- `docs/FRONTEND_TUI.md` for UX documentation (will need updates post-implementation).

## Open Questions / Follow-ups
- Preferred spinner style (ASCII spinner vs simple indicator)?
- Should elapsed time show seconds since `busy_since`? Confirm formatting.
- How to represent simultaneous tool calls (queue? assume single tool via sequential events?).
- Error messaging location when stream disconnects—footer vs top bar.

## Next Steps
- Draft plan (`plan.md`) defining new state fields, timer integration, UI rendering updates, and testing strategy.
- Validate assumptions with stakeholders if ambiguity remains.
