# Frontend TUI & Observer API

This guide covers the terminal UI shipped in `chat_tui/` and the observer
endpoints it depends on. The TUI provides a keyboard-driven experience for
starting runs, following streaming output, and switching between
conversations.

## Prerequisites

- Rust toolchain (`cargo`, `rustc`) for building the TUI.
- Python environment set up via `scripts/setup_env.sh` or `uv sync` (for the
  gateway/worker stack).
- Redis reachable at `REDIS_URL` when using the observer index.
- Optional: Docker/Docker Compose if you prefer `just up`.

Environment variables used by the TUI:

- `MAGENT2_BASE_URL` (defaults to `auto`, which resolves to
  `http://localhost:8000` or the Compose mapping).
- `MAGENT2_AGENT_NAME` (defaults to `DevAgent`).

Observer index toggles (see `magent2/observability/index.py` for details):

- `OBS_INDEX_ENABLED` (default `true`).
- `OBS_INDEX_TTL_DAYS` (default `7`).

## Launching the TUI

The recommended entry point is the Just recipe:

```bash
just tui
```

This recipe ensures the Docker stack (gateway, worker, Redis) is running before
building and launching the Rust TUI. If the TUI crate is missing, the recipe
exits with a helpful message so you can install it separately.

To run the TUI directly from the crate:

```bash
cd chat_tui
cargo run
```

The UI probes `GET /health` every ~750 ms and displays the gateway status in
the session tab bar. Streaming and conversation queries require the gateway
and observer index to be active.

## Layout

The screen is split into three rows:

1. **Session tabs** – show the current chat title and gateway status.
2. **Main content** – chat transcript; optionally includes a conversations
   panel on the left and agents/graph panes on the right.
3. **Input line** – compose the next user message.

Each chat session maintains its own input buffer, message history, stream task
and last SSE event ID.

### Conversations panel

Press `Ctrl+C` to toggle the conversations list. When visible, the panel queries
`GET /conversations` and displays up to 50 recent conversation IDs. Selecting
an item and pressing `Enter` clears the current transcript, starts streaming
from `/stream/{conversation_id}` (using `Last-Event-ID` when available), and
hides the panel. Use `Shift+Tab` to toggle focus between the input footer and
the conversations pane. Active panes add `[FOCUS]` to their titles and brighten
the border so you always know where navigation keys will land. Printable
characters always append to the input buffer regardless of focus.

Refreshing (`Ctrl+R`) re-fetches the list while preserving the selection when
possible.

### Agents pane

Press `Ctrl+A` to display the agents pane on the right-hand side. The pane polls
`GET /agents` roughly every three seconds, listing each agent’s active run
count, last-seen age, and the number of recent conversations tracked by the
observer index. When the index is disabled or empty, the pane shows placeholder
text instead of failing the UI.

### Conversation graph pane

Press `Ctrl+G` to show a per-conversation graph summary beside the chat view. When
enabled, the pane fetches `GET /graph/{conversation_id}` for the active chat
and refreshes it on session changes or roughly every five seconds. Nodes and
edges are rendered as a compact ASCII summary; large graphs are truncated after
120 edges with a rollover count. Missing or stale data is surfaced as a pane
message rather than interrupting the chat flow.

## Keybindings

| Binding            | Action |
|--------------------|--------|
| `Tab`              | Switch to the next chat session. |
| `F2`               | Open a new chat session. |
| `Enter`            | Send the current input (when the conversations panel is hidden). |
| `Enter` (with pane) | Load the highlighted conversation and begin streaming (pane must be focused). |
| `Esc`              | Quit the TUI (restores the terminal state). |
| `Ctrl+C`           | Toggle the conversations panel (focus moves to the panel when opened). |
| `Ctrl+R`           | Refresh conversations while the panel is visible. |
| `Shift+Tab`        | Toggle focus between the input field and conversations pane (when visible). |
| `Ctrl+A`           | Toggle the agents pane (auto-refreshes every ~3 s). |
| `Ctrl+G`           | Toggle the conversation graph pane (refreshes on session change / every ~5 s). |
| `Up` / `Down`      | Scroll chat history or, when the conversations pane is focused, move its selection. Leaving the bottom pauses follow mode. |
| `PageUp` / `PageDown` | Scroll chat faster or, with the pane focused, jump the selection by 10; scrolling up pauses follow mode. |
| `End`              | Jump to the latest message and resume follow mode. |
| `Ctrl+L`           | Clear the current session transcript. |
| `Ctrl+U`           | Clear the input buffer. |

The TUI also supports paste events (Bracketed Paste mode) and preserves
display scrollback before entering the alternate screen.

When the input footer has focus, the terminal cursor (classic blinking block)
is positioned at the next character slot inside the field so you can see where
typing will appear.

When follow mode is active (the default), new messages keep the chat pinned to
the bottom. Once you scroll away, the header shows “follow paused” until you
return to the bottom (e.g., via `End`, `PageDown`, or repeated `Down`).

## Streaming behaviour

- Messages are posted to `POST /send` with the configured agent name.
- Streaming responses come from `GET /stream/{conversation_id}`; the TUI parses
  server-sent events and appends `token`, `output`, and `tool_step` updates to
  the transcript.
- The last delivered SSE ID is persisted per session. When a stream is
  restarted, the TUI sets the `Last-Event-ID` header so history is not
  replayed.
- If the gateway is unreachable, the transcript logs a `[error] gateway unreachable`
  line instead of attempting the send.
- While awaiting the first token or during tool execution, the input footer
  shows a dot-based spinner and status message. The indicator clears on the
  next model token, stream completion, or error.

Agent replies are rendered with Markdown-aware formatting (lists, paragraphs),
and tool messages are styled distinctly.

## Observer API overview

The observer index powers the conversations panel, agents pane, and conversation
graph pane. The gateway exposes three read-only endpoints:

- `GET /conversations?limit=50&since_ms=<ts>`
  - Response: `{ "conversations": [ { "id": str, "last_activity_ms": int, "participants_count": int, "msg_count": int } ] }`
  - The `limit` parameter is clamped to 1–200; `since_ms` filters by last activity.
- `GET /agents`
  - Response: `{ "agents": [ { "name": str, "last_seen_ms": int, "active_runs": int, "recent_conversations": list[str] } ] }`
  - `recent_conversations` contains up to 50 conversation IDs per agent.
- `GET /graph/{conversation_id}`
  - Response: `{ "nodes": [ { "id": str, "type": "agent"|"user"|"other" } ], "edges": [ { "from": str, "to": str, "count": int } ] }`
  - Returns HTTP 404 when the conversation ID is unknown or expired.

When the index is disabled or Redis is unavailable, these endpoints return
empty collections. Callers should treat data as best-effort and resilient to
partial updates.

## Validation and quality gates

- Run `just check` before sending a PR. This includes Python linters/tests and,
  when available, `cargo fmt` and `cargo clippy` for the TUI crate.
- For targeted docs or UI changes, `cargo fmt` (for Rust) and `markdownlint`
  are the common failure points recorded in `reports/`.

## Troubleshooting

- **Gateway status shows `down`:** ensure the gateway process is reachable at
  `MAGENT2_BASE_URL/health` and that Docker ports are mapped correctly.
- **Conversations panel is empty:** confirm `OBS_INDEX_ENABLED` is `true` and
  the observer index is writing conversation metadata to Redis.
- **Stream does not resume:** check that the gateway’s SSE endpoint forwards
  `Last-Event-ID`. Old messages may be trimmed from Redis streams based on the
  configured max length.

For additional architectural background, see `docs/PRD.md` and
`docs/HANDOVERS/feat-client-v1-polish.md` for client plans.
