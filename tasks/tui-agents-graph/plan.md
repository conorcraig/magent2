# Plan: TUI agents + graph panes

## Scope
- Implement agents pane and conversation graph pane toggles in the Rust TUI.
- Poll `/agents` periodically and render summarized status.
- Fetch `/graph/{conversation_id}` for active session on demand and render ASCII summary with safe caps.
- Ensure panes degrade gracefully when observer data is unavailable.

## Acceptance Criteria
- `a` key toggles agents pane; updates every ~3s with agent name, active runs, last seen age.
- `g` key toggles graph pane; shows nodes (id/type) and edges (from→to count) for current conversation; handles large graphs without hanging the UI.
- Existing chat/conversations functionality remains responsive (no regressions in keybindings or streaming).
- `just check` passes (cargo fmt/clippy, pytest, etc.).

## Implementation Steps (draft)
1. Extend `AppState` with new pane state (flags, data vectors, timestamps).
2. Add polling logic for `/agents` (likely within main loop with throttle based on `Instant`).
3. Implement fetch + render helpers for agents pane (formatting, empty/error states).
4. Implement fetch + render helpers for conversation graph (respect edge caps, fallback messages).
5. Wire keybindings (`a`, `g`) to toggle panes and trigger fetches.
6. Update `render_ui` to layout panes (split area when active, similar to conversations panel).
7. Add unit/component tests if feasible (e.g., for formatting helpers) and run full gate.

## Validation
- Manual run of TUI against local gateway with seeded observer data.
- Confirm panes display empty-state messaging when index disabled or endpoints return empty arrays.
- Ensure no new clippy warnings; run `cargo fmt`, `cargo clippy`, `just check`.

## Risks / Mitigations
- Overloading layout with too many panes → ensure layout logic handles multiple panes gracefully or restrict to one pane visible at a time.
- Latency from HTTP polling → throttle requests, reuse client.
- Graph rendering clarity → keep formatting simple; consider summarizing when exceeding caps.

## Open Items Before Implementation
- Decide on refresh interval constants (configurable vs hard-coded).
- Clarify whether agents/graph panes can be shown simultaneously with conversations list (may need prioritization rules).
- Determine data structure for graph summarization (e.g., limit edges >100 by truncation + count of omitted edges).
