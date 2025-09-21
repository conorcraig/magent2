# Plan

## Goal

Implement per-session follow mode for the chat pane so the view auto-scrolls when at the bottom and respects manual scrollback, aligning with issue #161.

## Approach

1. **State additions**
   - Extend `ChatSession` with `follow: bool`, `max_scroll: u16`, and `viewport_height: u16` while keeping the existing top-based `scroll` offset.
   - Initialize the new fields for all sessions (default to follow enabled and zero offsets) and reset them appropriately (e.g., when clearing messages).

2. **Input handling**
   - Update key handling (`handle_key_event`) so scroll inputs adjust `scroll` but also toggle `follow` off whenever the offset is below `max_scroll`.
   - Add detection for returning to the bottom: when scrolling down reaches `max_scroll`, re-enable follow; map the `End` key to jump to the bottom and set follow true.

3. **Message append behavior**
   - Leave offsets untouched when follow is disabled; when follow is enabled ensure `scroll` stays aligned with the latest `max_scroll` so new messages remain visible.

4. **Rendering adjustments**
   - During render, compute the wrapped line count for the active session (mirroring parsing logic) based on the current viewport width/height.
   - Derive `max_scroll` from the line count, clamp the stored `scroll` to that maximum, and when `follow` is true force `scroll` to `max_scroll` before passing it to `Paragraph::scroll`.
   - Update the chat title to hint when follow mode is paused (e.g., instruct to press End).

## Acceptance Criteria

- New messages auto-scroll when the user hasn’t scrolled up.
- Manual scroll inputs stop auto-follow and maintain the previous viewport.
- Scrolling back to the bottom (via Down/PageDown/End) resumes auto-follow.
- No regressions to input focus or other panes.

## Validation

- Manual: Launch `just tui`, send messages, scroll up/down, verify follow toggles correctly.
- Automated: None (UI behavior).

## Risks & Mitigations

- **Wrapped line counting**: Ratatui doesn’t expose final layout; we’ll approximate using line widths and viewport width. Keep the logic mirrored with rendering to avoid drift.
- **Performance**: Recomputing line counts each render could be heavy; limit to the active session and reuse existing parsing work.
- **Edge cases**: Empty sessions or tiny viewports; ensure offsets clamp safely and follow remains consistent.

## Status

- Implemented in `chat_tui/src/main.rs` with per-session follow state, clamped scroll math, and UI feedback.
- Manual validation: `just tui` (user confirmed follow toggles and auto-scroll behaviour).
