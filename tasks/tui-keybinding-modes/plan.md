# Plan

## Scope

Address keybinding focus UX, visible focus indicators, input cursor, and chat scrolling reliability for the Rust TUI.

## Approach

1. **Focus indicators**: Augment the conversations pane and input footer titles with explicit focus tags (e.g., `[FOCUS]`) and ensure styling stands out irrespective of color themes.
2. **Input cursor**: Use Ratatui's `Frame::set_cursor` to position the terminal cursor at the current input location when the input has focus; hide or park it otherwise so a classic blinking block is visible where typing occurs. Support Unicode width for accurate cursor placement.
3. **Scrolling bug**: Revisit scroll calculations—replace the manual width-based estimation with Ratatui's wrapping logic (`Paragraph::line_count`) to calculate total rows so the bottom of the conversation is always reachable, even during streaming updates.
4. **Docs**: Update README / TUI guide to mention the new focus badges and cursor behaviour.
5. **Validation**: `cargo fmt` and `cargo check` for `chat_tui`; reason through rendering changes (manual run optional but recommended for QA).

## Acceptance Criteria Mapping

- Focused component is visually obvious (not just border color) and stays in sync with state.
- Input field shows a blinking cursor at the correct position whenever it has focus; printable characters still go to input.
- Chat scroll reliably reaches the latest messages; no off-screen lingering content.
- Documentation reflects bindings and focus/cursor behaviour.

## Risks / Mitigations

- Ratatui `line_count` API is unstable; ensure feature flag is enabled or replicate behaviour carefully.
- Cursor positioning must account for grapheme width; rely on `unicode-width` to avoid drift.

## Validation

- `cargo fmt --manifest-path chat_tui/Cargo.toml`
- `cargo check --manifest-path chat_tui/Cargo.toml`
- Manual run (`cargo run`) suggested to visually confirm focus and cursor cues.
