# magent2 – Product Requirements Document (PRD)

## 1. Vision

Build a general-purpose, well-tooled software agent that can operate solo or as part of dynamic, hierarchical teams. Each agent is a Swiss-army knife (no narrow roles) differentiated only by hierarchy and responsibilities. Agents coordinate via signals and a shared bus, can spawn sub-agents, work in parallel, and safely manipulate local files and git worktrees. They run with strong sandboxing, have MCP access to external capabilities, and support rich, monitorable streaming with clear accountability (who is doing what, where).

## 2. Goals (Must-Have)

- One general agent type with:
  - Tooling: terminal, files, MCP, chat (agent-to-agent), signals (send/wait), optional web search.
  - Monitoring: token/tool/output events, signals, and handoffs surfaced over SSE.
  - Sandboxing: cwd constraints, deny path escapes, allowlist/quotas/timeouts, redaction.
  - File I/O: read/write/edit within allowed scopes.
  - MCP: connect to configured stdio servers; expose allowlisted tools.
- Teaming & coordination:
  - Spawn sub-agents for parallelizable chunks; delegate serial work.
  - Signals to idle without ending a turn; resume on signal.
  - Registry of agents, roles, and file ownership (who works on what).
  - Window person defined per team for escalation/unblock.
- Git worktrees:
  - Allocate per-agent worktrees and allowed files to avoid conflicts.
  - Enforce file-scope policy at tool layer.

## 3. Non-Goals (Now)

- Specialized agent roles.
- Cross-org identity, billing, or multi-tenant auth.
- Cloud orchestration beyond Redis bus + processes.

## 4. Users and Use Cases

- Solo developer: give a task; the agent splits into sub-agents, runs in parallel, and returns results with diffs and logs.
- Team coordination: agents work on distinct file scopes; signal when blocked; escalate to window person.
- Air-gapped ops: all tools usable with no outbound internet (MCP servers/filesystem only).

## 5. Functional Requirements

- Agent runtime
  - Single agent definition; configurable model/instructions/tools.
  - Streaming over SSE: tokens, tool steps, outputs, agent updates, signals.
  - Persistent sessions optional (LRU in-process by default; SQLAlchemy if available).
- Tools
  - Terminal tool: allowlist, timeouts, output caps, redaction, sandbox cwd.
  - Files tool: list/read/write/edit, guarded by allowed paths.
  - MCP gateway: spawn stdio servers from env; allowlist tool exposure per agent.
  - Chat tool: message conversations or agents via bus topics.
  - Signals tool: send/wait to coordinate without ending turn.
  - Optional web search tool and output guardrail for citations (enable when connected).
- Teaming & registry
  - Registry tracks agents, responsibilities, allowed files/paths, worktree locations, and window person.
  - Spawn API: create sub-agent with inherited config + narrowed scope.
  - Policy: attempts to edit outside allowed scope are denied with actionable error.
- Git worktrees
  - Create per-agent worktree (branch naming convention).
  - Map allowed file scopes to worktree; preflight for conflicts.
- Sandboxing & safety
  - Enforce cwd rooting; deny traversal; rate limits and quotas.
  - Redact secrets by label/pattern; deny dangerous commands.

## 6. Monitoring & Observability

- Stream events:
  - token, tool_step(start/finish), output, agent_updated, signal(send/receive).
- Logs/metrics:
  - run_started/run_completed, runs_errored; per-tool latency and failures.
- Trace (later): optional spans for key steps.

## 7. Configuration

- Env-driven (defaults safe):
  - AGENT_NAME, AGENT_MODEL, AGENT_INSTRUCTIONS(_FILE), AGENT_TOOLS.
  - AGENT_MAX_TURNS.
  - MCP: AGENT_<Name>_MCP_<N>_CMD/ARGS/CWD/ENV_JSON/ALLOW/BLOCK.
  - Redis URL; sandbox root; redaction/conf policy.

## 8. Acceptance Criteria

- A single agent can:
  - Split a task, spawn >=2 sub-agents, coordinate via signals, and merge results.
  - Respect file scope; denied edits outside scope; safe terminal/file operations.
  - Operate with MCP tools (local memory/filesystem server) in offline mode.
  - Stream progress: tokens, tool calls, signals; visible via `/stream/{conversation_id}`.
- Tests: signals, MCP gateway smoke, sandbox policies, worktree allocation, file-scope enforcement.
- Docs: setup, env, safety, and a worked team demo.

## 9. Phased Delivery

- Phase 1 (Baseline)
  - Signals tool: send/wait + tests; wire as default tools.
  - Chat tool (exists); terminal tool hardened (exists).
  - MCP gateway (exists) — ensure docs and sample configs.
  - SSE includes token/tool/output (exists); expose signal events.
- Phase 2 (Teaming)
  - Team registry (in-memory + env); window person; allowed file scopes.
  - Spawn sub-agents API + lifecycle; per-agent config overlay.
  - Git worktrees: create/cleanup; map file scope; conflict preflight.
- Phase 3 (Policy & Web)
  - Optional WebSearch tool; output guardrail requiring citations when used.
  - Stream agent_updated events; richer progress messages.
  - Optional SQLAlchemy sessions for persistence.

## 10. Open Questions

- How should parent agent reconcile/merge sub-agent outputs? (first version: deterministic file ownership + simple join).
- Retry/backoff strategy for signals and long-running tasks.
- Quotas per agent/team for terminal/files tool usage.

## 11. Risks & Mitigations

- Tool misuse -> strict allowlists, scopes, redaction, and quotas.
- Merge conflicts -> per-agent worktrees and explicit file scopes.
- Resource leaks (spawned agents) -> lifetimes tracked in registry; cleanup on idle.

## 12. Glossary

- Signal: lightweight bus message used to wake/coordinate agents.
- Window person: the designated responsible contact for a team.
- Worktree: per-agent git working directory bound to a branch.

---

## Appendix A: Signals – Coordination Design

### A.1 Purpose

Provide first-class, minimal primitives for agent synchronization without ending a turn, enabling parallel fan-out/fan-in, readiness barriers, and fine-grained unblocking.

### A.2 Concepts

- Signal topic: arbitrary string (convention: `signal:<team>/<scope>/<event>`).
- Payload: small JSON object (avoid large blobs); intended for machines.
- Cursor: `message_id` returned by publish; used as `last_id` to resume.
- Timeout: millisecond budget for `wait`; caller decides whether to keep waiting.

### A.3 APIs

- `signal_send(topic: str, payload?: dict) -> {ok, topic, message_id}`
- `signal_wait(topic: str, last_id?: str, timeout_ms: int) -> {ok, topic, message|timeout, message_id?}`
  - On timeout: `{ok: false, topic, timeout_ms, last_id}`

### A.4 Semantics / Guarantees

- At-least-once delivery (via Bus semantics); consumers must de-duplicate using `message_id`.
- Ordering per topic is preserved by using `last_id` to advance the cursor.
- Backpressure via timeouts; callers choose retry/backoff.
- Signals are orthogonal to chat; no UI text or conversation routing.

### A.5 Patterns

- Barrier (fan-in): parent waits on `N` distinct `done` topics then proceeds.
- Readiness: child emits `ready`; parent unblocks and sends work details.
- Rendezvous: two peers wait for each other’s `ready`; then both continue.
- Progress heartbeats: children emit `progress` signals; parent adapts timeouts.

### A.6 Example Topics

- `signal:build/ready`, `signal:teamA/job42/done`, `signal:repo/file-A.lock/released`

### A.7 Failure Handling

- Timeouts: return structured timeout; the agent can re-issue `wait` with same `last_id`.
- Lost wakeups: avoided by cursors; signals sent before `wait` are still seen if `last_id` permits.
- Duplicate processing: caller must track processed `message_id`s.

### A.8 SSE/Monitoring

- Add `signal_send` and `signal_recv` events to stream for visibility.
  - Fields: `topic`, `message_id`, (for recv) minimal `payload` length.

### A.9 Security & Policy

- Topic allowlist/prefix policy (e.g., restrict to `signal:<team>/...`).
- Payload size cap; redact sensitive keys by label/pattern.
- Rate limiting per agent/team.

### A.10 Integration Points

- Orchestrator: use signals to coordinate sub-agents; resume parent mid-turn.
- Tools: combine `signal_wait` with `chat_send` for status updates on timeout.
- Registry: map topics to owners (who is responsible) and escalation (window person).

### A.11 Tests

- Roundtrip send/wait; ordering with `last_id`; timeout path.
- Concurrency: simultaneous senders; ensure per-topic ordering observed.
- Policy: deny disallowed topics; enforce payload size limits (to be implemented).

### A.12 Future Enhancements

- Multi-wait (`wait_any(topics[])`, `wait_all(topics[])`).
- Broadcast with acks (`signal:.../ack/<agent>`); simple quorum.
- Persist cursors in session for long-lived conversations.

---

## Appendix B: Information Flow & Communication Patterns

This maps the user's flow models to concrete constructs in magent2.

### B.1 One-to-Many

- Broadcast: `chat:{conversation_id}`; targeted broadcast via agent group topics `chat:team/<name>`.
- Multicast: topic naming for subgroups; policy controls who can publish.

### B.2 One-to-One

- Direct push: `chat_send(recipient="agent:<Name>")`.
- Pull/query: agent uses MCP/files tools to fetch on demand; optional polling via `signal_wait` for readiness.

### B.3 Many-to-One

- Aggregation: parent collects children results; signals for `done` fan-in; registry records contributors.
- Consensus: future — `signal:.../ack/<agent>` with quorum rules.

### B.4 Many-to-Many

- Mesh exchange: agent-to-agent chat topics; loosely-coupled conventions.
- Gossip: not default; can simulate by forwarding messages to neighbor lists.
- Publish–Subscribe: Redis bus topics; consumers subscribe to `chat:*`, `signal:*` prefixes.

### B.5 Hierarchical

- Tree top-down: parent → children via chat/commands; signals for phase gates.
- Tree bottom-up: children → parent via aggregation topics and `done` signals.

### B.6 Sequential / Pipeline

- Chain/relay: orchestrator sequences tool calls/handoffs; signals gate transitions.

### B.7 Cyclic / Feedback

- Closed loop: parent evaluates progress, sends adjustments; children heartbeat via signals.

### B.8 Store-and-Forward

- Repository: code/doc edits committed to worktrees; artifacts discoverable; links shared via chat.

### B.9 Event-Driven

- Signal/interrupt: `signal_send`/`signal_wait` for state transitions; SSE surfaces events in UI.

### B.10 Probabilistic / Emergent

- Diffusion/stigmergy: notes/RFCs in repo; agents consult docs (MCP/files) and leave traces (PRs, docs).

### B.11 Decision Rules (Operational)

- Few recipients, high stakes → direct push (chat 1:1) + acknowledgement.
- Many recipients, low coupling → pub-sub topics; short, structured payloads.
- Unknown recipients, durable need → store-and-forward (docs), then broadcast link.
- Rapid change → event-driven signals; bounded topics per team to avoid flood.
- Complex transformation → pipeline with explicit gates (signals + reviews).

### B.12 Metrics

- Lead time, error escape rate, MTTR, reuse/participation, “no surprises” score, blast radius of disclosure — instrumented via logs/metrics and sampled SSE events.
