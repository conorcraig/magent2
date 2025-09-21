# Plan: Streaming client library promotion

## Scope

- Refactor `magent2/client/cli.py` into a reusable library module plus a slim CLI entry point.
- Preserve existing CLI behaviours (quiet/JSON output, resume, tool-step context) while exposing a public API for programmatic use.
- Update documentation and references now that `magent2/cli.py` is gone.

## Acceptance Criteria

- New module (e.g., `magent2/client/streaming.py`) exports clearly documented functions/classes for sending messages and consuming SSE events.
- CLI wrapper (`magent2/client/cli.py`) delegates to the library and retains current CLI flags.
- Unit/integration tests cover streaming iterator behaviour and CLI invocation.
- README/docs updated to point to the new library and CLI usage.
- `just check` passes.

## Implementation Steps (draft)

1. Design API surface (e.g., `StreamingClient` class with `send` + event iterator).
2. Extract existing logic from `cli.py` into the new module with minimal behaviour changes.
3. Update CLI to import and use the new abstraction; keep argument parsing stable.
4. Add tests (mocked SSE responses, CLI invocation smoke test).
5. Update docs (`README.md`, `docs/FRONTEND_TUI.md` if needed) plus `pyproject.toml` notes.
6. Wire `pre-commit`/`just` steps if new files required.

## Validation

- Run targeted unit tests plus `just check`.
- Manual smoke test: `magent2-client --message "ping" --json` against local stack.

## Risks / Mitigations

- API churn may break scripts → keep compatibility layer & document changes.
- SSE handling is stateful → ensure tests cover reconnect/resume paths.
- New module must avoid circular imports with other clients → keep dependencies minimal.

## Open Items

- Confirm naming/location for the reusable library (package path, public API).
- Decide whether to support async usage now or later.
- Determine if CLI should gain subcommands or remain flag-based for this iteration.
