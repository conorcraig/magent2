# Research Notes

## Existing Scroll Handling

- Chat sessions live in `chat_tui/src/main.rs` with `ChatSession` containing a `scroll: u16` offset (top-based) and no follow flag.
- The chat view renders via `Paragraph::scroll((session.scroll, 0))`, so larger `scroll` values push the viewport further down the transcript.
- Key handling (`handle_key_event`) adjusts `session.scroll` on `Up/Down/PageUp/PageDown` without any awareness of new message arrival. `scroll` increments regardless of content height and never auto-resets when new messages append.

## Message Append Flow

- Incoming user/model/tool messages are pushed to `session.messages` inside the async UI event loop (e.g. `UiEvent::ModelOutput`, `UiEvent::ModelToken`).
- No logic currently adjusts `scroll` or any follow state when messages arrive, so the viewport stays fixed at whatever offset the user last set.

## Acceptance Criteria Highlights (#161)

- Maintain a "follow" mode that keeps the view pinned to the bottom when the user hasn’t scrolled away.
- Scrolling up disables follow until the user returns to the bottom (via scrolling down/page down/end).
- Behavior should be per-session so each tab tracks its own follow mode.

## External Research

- **Tail behaviour requirements**: Ratatui issue [#625](https://github.com/ratatui/ratatui/issues/625) and the earlier tui-rs issue [#89](https://github.com/fdehau/tui-rs/issues/89) confirm that widgets do not expose their post-wrap height, so applications must track scroll offsets explicitly to implement follow mode.
- **UX pattern (follow vs browse)**: The [tui-logger crate](https://docs.rs/tui-logger/latest/tui_logger/) toggles between live-follow and paged browsing (PageUp to pause, Escape to resume), supporting the plan to keep a per-session `follow` boolean that only re-enables when the user returns to the bottom.
- **Paragraph API constraints**: `Paragraph::scroll((y, x))` expects an offset from the top; community examples set `y = total_lines - viewport_height` when follow mode is active, recomputing the wrapped line count at render time.
- **Session-local metadata**: Ratatui chat demos (e.g., Chabeau TUI) store scroll offsets per tab to avoid shifting views when switching sessions, mirroring our approach to keep follow state within `ChatSession`.
