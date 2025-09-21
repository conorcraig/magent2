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

The recommended entry point is the project CLI:

```bash
magent2 run
```

When `chat_tui/` is present, this command builds and launches the Rust TUI.
If the TUI is unavailable, the Python streaming client is used as a fallback.

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
   panel on the left.
3. **Input line** – compose the next user message.

Each chat session maintains its own input buffer, message history, stream task
and last SSE event ID.

### Conversations panel

Press `c` to toggle the conversations list. When visible, the panel queries
`GET /conversations` and displays up to 50 recent conversation IDs. Selecting
an item and pressing `Enter` clears the current transcript, starts streaming
from `/stream/{conversation_id}` (using `Last-Event-ID` when available), and
hides the panel.

Refreshing (`r`) re-fetches the list while preserving the selection when
possible.

## Keybindings

| Binding            | Action |
|--------------------|--------|
| `Tab`              | Switch to the next chat session. |
| `F2`               | Open a new chat session. |
| `Enter`            | Send the current input (when the conversations panel is hidden). |
| `Enter` (with panel) | Load the highlighted conversation and begin streaming. |
| `Esc`              | Quit the TUI (restores the terminal state). |
| `c`                | Toggle the conversations panel. |
| `r`                | Refresh conversations while the panel is visible. |
| `Up` / `Down`      | Scroll chat or move selection in the panel. |
| `PageUp` / `PageDown` | Scroll chat faster or jump the panel selection by 10. |
| `Ctrl+L`           | Clear the current session transcript. |
| `Ctrl+U`           | Clear the input buffer. |

The TUI also supports paste events (Bracketed Paste mode) and preserves
display scrollback before entering the alternate screen.

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

Agent replies are rendered with Markdown-aware formatting (lists, paragraphs),
and tool messages are styled distinctly.

## Observer API overview

The observer index powers the conversations panel and upcoming panes (agents,
conversation graph). The gateway exposes three read-only endpoints:

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
