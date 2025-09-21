# Plan: TUI typing/status indicator

## Scope
- Add per-session busy state (waiting for stream / running tool) to the Rust TUI.
- Render a spinner + status text in the input/footer area without stealing focus.
- Clear status when first content event arrives or tool completes/fails.
- Handle stream errors by replacing spinner with error message.

## Acceptance Criteria
- Indicator appears within ~100ms after `/send` and clears on first token/output event.
- Tool spinner shows `Tool: <name>` and elapsed time during tool steps; clears on completion/failure.
- No input lag or layout jitter; behaviour remains per session (independent tabs).
- Graceful recovery on stream disconnects (spinner replaced with error notice).

## Implementation Thoughts (draft)
1. Extend `ChatSession` with `is_busy`, `busy_reason`, `busy_since`, and `spinner_phase`.
2. Update send logic to set busy state immediately after POST `/send` succeeds; clear on errors.
3. Hook SSE handler to manage busy state transitions on `token`, `output`, and `tool_step` events.
4. Add timer in main loop (e.g., `tokio::time::interval`) or compute spinner frame based on elapsed time.
5. Render footer line to include spinner + status while busy.
6. Add tests for helper functions (e.g., spinner frame calculation, busy state transitions) if practical.

## Validation
- Manual verification with local gateway (simulate long tool steps via test runner).
- Run `just check`.
- Update docs (`docs/FRONTEND_TUI.md`) with new indicator behaviour.

## Risks / Mitigations
- Spinner updates causing flicker: compute text deterministically based on elapsed time rather than relying on frequent redraws.
- Concurrent tool events: ensure reason updates handle start/end properly and avoid stale states.
- Timer overhead: reuse existing loop with `Instant` comparisons instead of spawning new tasks.

## Open Items
- Decide spinner characters (e.g., `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` vs ASCII `|/-\`).
- Determine error message wording for disconnects.
- Confirm behaviour when user cancels/aborts a stream (clear busy state?).
