## Summary

This PR advances Issue #70 (Orchestrated multi-agent teams) while keeping the system single-agent-typed. Any agent instance can orchestrate by calling tools; no separate orchestrator class is introduced.

## Scope and Design

- Single agent type; orchestration is behavior via tools:
  - `chat_send`: spawns sub-agents as new conversations of the same agent.
  - `signals`: coordinates phase gates and completion without ending turns.
  - `TeamRegistry`: assigns responsibilities and allowed path scopes.
  - `Branch allocation`: per-child branch (no worktrees).
- Observability: signal send/recv events and existing stream events (token/tool_step/output).

## Changes

- Teaming
  - Add `magent2/team/registry.py`: in-memory team registry, window person, path ownership.
  - Add `magent2/team/branch.py`: safe branch allocator (`feature/{ticket}/{agent}`).
- Signals and child completion
  - Worker optional auto-complete: when child receives `done_topic=...` hint and `AUTO_CHILD_SIGNAL_DONE=1`, it emits a `signal:...:done`.
- Orchestration helper
  - Add `magent2/tools/orchestrate.py`:
    - `orchestrate_split(task, num_children, ...)` publishes subtask kickoffs to `agent:DevAgent`, encodes hints in message content, and can `wait` on `signal_wait_all`.
    - Function-tool wrapper `orchestrate_split_tool` is behind `ENABLE_ORCHESTRATE_TOOL=1` to avoid SDK schema conflicts during tests.
- Chat tool
  - Kept public SDK function signature stable; orchestration hints are encoded in content when needed.
- Demo/Responses runner
  - Added a minimal OpenAI Responses runner (unused unless Agents runner is unavailable); guarded by selection logic. No impact on existing tests.

## Environment Flags

- `AUTO_CHILD_SIGNAL_DONE=1`: enable child auto "done" signal when `done_topic=...` is found in the message text.
- `ENABLE_ORCHESTRATE_TOOL=1`: register `orchestrate_split_tool` as an Agents SDK function tool.

## What’s NOT in scope

- No separate orchestrator class; behavior is expressed via tool calls.
- Web search integration is unchanged (left optional as per PRD).

## Tests

- New:
  - Team registry: `tests/test_team_registry.py`.
  - Branch allocator: `tests/test_branch_allocator.py`.
- Existing suites all pass locally (ruff, mypy, pytest). CI should remain green.

## Risks and Mitigations

- Tool schema compatibility: the orchestrate function tool is env-gated to avoid breaking the SDK function schema during tests.
- Child auto-signal parsing: guarded by env and conservative; only triggers if `done_topic=` is present.

## Reviewer Checklist

- [ ] Validate that orchestration approach aligns with Issue #70 (single agent type orchestrating via tools).
- [ ] Confirm tests pass and CI is green.
- [ ] Review `TeamRegistry` path ownership semantics (glob specificity).
- [ ] Review branch naming/creation safety.
- [ ] Consider documentation follow-ups (quickstart for orchestration flags, demo instructions).