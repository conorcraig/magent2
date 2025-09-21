# Research Notes

## Context

- Issue #162 requests non-printable shortcuts and explicit focus handling to avoid conflicts while typing in the input field.
- Current key handling lives in `chat_tui/src/main.rs`, `handle_key_event` (~line 467).

## Observations

- Plain-character shortcuts exist for `c` (toggle conversations), `r` (refresh conversations), `a` (toggle agents), `g` (toggle graph).
- Focus is implicit: arrow/Page keys automatically control the conversations list whenever it is visible, otherwise they scroll chat history.
- UI render places the conversations panel title text `"Conversations (c to toggle)"` and input title `"Input (Enter to send, Tab switch, F2 new, Esc quit)"`.
- No current visual indicator differentiates focus between input and panels.
- `Tab` cycles chat sessions; `F2` opens a new session, `Esc` exits; `Ctrl+L` and `Ctrl+U` already provide control-based commands.

## Constraints & Considerations

- Avoid regressing session switching (`Tab`) and other existing shortcuts.
- Maintain compatibility with existing SSE/task logic; changes limited to input handling and rendering metadata.
- Need to update README keybinding section to reflect new shortcuts/modes.
- Highlighting focus can leverage `Block::border_style` (ratatui) for input panel and conversations list.
- Consider fallback when conversations panel hidden: focus should default to input.
