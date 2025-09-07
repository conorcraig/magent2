## Handover: Integrate OpenAI Agents SDK Runner into Worker (#33)

Owner: next agent picking up Issue #33

### Context

- Goal: Implement a real Runner using the OpenAI Agents SDK; map streamed SDK events → `TokenEvent`/`ToolStepEvent`/`OutputEvent`; maintain session per `conversation_id`; echo fallback when `OPENAI_API_KEY` is unset.
- Contracts v1 are frozen: see `magent2/models/envelope.py` and `docs/CONTRACTS.md`. Do not change event shapes or envelope fields.
- Current state:
  - `magent2/worker/worker.py` defines the `Runner` protocol and `Worker` loop. `Worker._run_and_stream` iterates a synchronous iterator from `runner.stream_run(envelope)` and publishes each event to `stream:{conversation_id}`.
  - `magent2/worker/__main__.py` uses an `EchoRunner` placeholder.
  - Gateway publishes inbound to both `chat:{conversation_id}` and `chat:{agent_name}`; streams SSE from `stream:{conversation_id}`. Tests cover this.
- References: see `docs/refs/openai-agents-sdk.md` (SDK docs index) and the upstream docs site.

### Deliverables

- SDK-backed Runner implementing the `Runner` protocol.
- Streaming mapping from SDK events to our v1 events (`TokenEvent`, `ToolStepEvent`, `OutputEvent`) with preserved ordering and `conversation_id`.
- Session management keyed by `conversation_id` (reused across runs).
- Runner selection: echo fallback if `OPENAI_API_KEY` is missing; otherwise use SDK-backed runner.
- Tests for event mapping, session reuse, and runner selection.

### High-level design

1) Runner adapter

- Add module `magent2/runner/openai_agents_runner.py` with class `OpenAIAgentsRunner` implementing the `Runner` protocol (`stream_run(self, envelope)` returns a Python iterator).
- Bridge async SDK streaming → sync iterator:
  - Start an asyncio task in a dedicated thread that drives the SDK's streaming API (see docs: Runner.run_streamed → result.stream_events()).
  - Use a `queue.Queue` to hand over mapped events to the sync generator; yield from the queue until a sentinel indicates completion. This preserves streaming semantics within our existing synchronous `Worker` loop.

2) SDK agent/session configuration

- Add `magent2/runner/config.py` to load environment:
  - `OPENAI_API_KEY` — presence toggles SDK vs echo.
  - `AGENT_NAME` — existing.
  - `AGENT_MODEL` — model name for the SDK (string).
  - `AGENT_INSTRUCTIONS` or `AGENT_INSTRUCTIONS_FILE` — inline text or path loaded at startup.
  - Optional: `AGENT_TOOLS` (comma-separated) — initially unused or empty to avoid speculative tool wiring; safe default is none.
- Construct an SDK `Agent` from `agents` package using model/instructions/tools.
- Maintain `self._sessions: dict[str, Session]` in the runner; get-or-create per `conversation_id`. Consider simple bound LRU (e.g., 256) to avoid unbounded memory.

3) Event mapping (SDK → v1 events)

- TokenEvent: map low-level model text delta events. In SDK streaming, these surface via a "raw response" stream event carrying a `ResponseTextDeltaEvent` (see docs examples). Convert each delta to `TokenEvent(conversation_id, text, index)` where `index` monotonically increments per run.
- ToolStepEvent: map tool call/return milestones. In SDK streaming, these appear as run-item events. Emit a `ToolStepEvent` when a tool is invoked with `name` (e.g., function name) and `args` (parsed JSON/dict). When a tool returns, emit a `ToolStepEvent` with `result_summary` (shortened string or status). Keep args/result small; avoid dumping large payloads.
- OutputEvent: when final agent output becomes available (end of run), emit `OutputEvent(conversation_id, text=final_text, usage=? if exposed by SDK)`. If usage metrics are available in the result, attach as a small dict.
- Preserve ordering exactly as produced by the SDK stream; the queue handoff preserves order.

4) Runner selection wiring

- Update `magent2/worker/__main__.py` to select a runner at startup:
  - If `OPENAI_API_KEY` is unset → use `EchoRunner` (existing behavior).
  - Else → instantiate `OpenAIAgentsRunner(config=...)`.
  - No other behavior changes; `Worker` stays synchronous.

5) Tests (TDD additions)

- New tests (no network; stub SDK):
  - `tests/test_runner_sdk.py`:
    - Test mapping of a minimal synthetic SDK event stream to our three event types (token, tool_step, output). Provide a fake `ResultStreaming` object with an async `stream_events()` yielding synthetic SDK-like event dicts/objects; the runner adapter maps them to our Pydantic events. Validate shape and order.
    - Test session reuse: run two envelopes with same `conversation_id` and assert the runner caches a session object (exposed via a testing hook or via `len(runner._sessions)` assertions in test-only mode).
  - `tests/test_worker.py` additions:
    - Test runner selection logic in `__main__`: monkeypatch env to unset/set `OPENAI_API_KEY` and ensure appropriate class is chosen.

### Implementation notes

- Imports (SDK): per docs examples, the package exposes `Agent`, `Runner`, and streaming event classes from the top-level `agents` module. Validate against `docs/refs/openai-agents-sdk.md` and upstream docs.
- Do not introduce any changes to v1 contracts. Only add new code and wire-up.
- Keep runner mapping defensive: accept both typed objects and dict-shaped events; ignore unrecognized event types; log at debug level if needed (logging infra may land later; keep silent or minimal).
- Keep tool support minimal initially. If no tools are configured, the agent will operate as pure chat.

### File changes (planned)

- Add `magent2/runner/openai_agents_runner.py`
- Add `magent2/runner/config.py`
- Edit `magent2/worker/__main__.py` (runner selection)
- Add tests: `tests/test_runner_sdk.py` (+ small updates in `tests/test_worker.py` as needed)

### Pseudocode sketch (adapter outline)

```python
class OpenAIAgentsRunner(Runner):
    def __init__(self, agent: Agent, *, session_limit: int = 256) -> None:
        self._agent = agent
        self._sessions: dict[str, Session] = {}
        self._session_order: deque[str] = deque()
        self._session_limit = session_limit

    def _get_session(self, conversation_id: str) -> Session:
        # get-or-create and manage simple LRU
        ...

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        # Create a thread + queue; run async streaming in the thread
        # Enqueue mapped events; yield from queue until sentinel
        ...

    async def _run_streaming(self, envelope: MessageEnvelope, queue: Queue) -> None:
        session = self._get_session(envelope.conversation_id)
        result_stream = Runner.run_streamed(self._agent, input=envelope.content or "", session=session)
        token_index = 0
        async for ev in result_stream.stream_events():
            mapped = self._map_event(envelope.conversation_id, ev, token_index)
            if isinstance(mapped, TokenEvent):
                token_index += 1
            if mapped is not None:
                queue.put(mapped)
        # finally, enqueue OutputEvent if provided by the stream result API (or when seen)
        ...

    def _map_event(self, conversation_id: str, ev: Any, token_index: int) -> BaseStreamEvent | None:
        # Map SDK event → TokenEvent/ToolStepEvent/OutputEvent
        ...
```

### Risks and mitigations

- SDK API drift: Keep mapping tolerant to shape differences; prefer feature checks over strict isinstance to avoid tight coupling.
- Blocking behavior: The adapter uses a thread to avoid blocking the main loop; ensure proper teardown and sentinel signaling.
- Large tool args/results: Truncate or summarize in `result_summary` to limit event size.

### Out of scope (for this issue)

- Tool implementations (terminal/todo/MCP) and guardrails.
- Handoffs between multiple agents.
- Observability/tracing wiring.

### Validation

- Run locally: `just check` (format, lint, types, complexity, secrets, tests).
- Manual smoke: run gateway + worker; send a message; stream SSE and observe token → output events. Without `OPENAI_API_KEY` set, output should echo input.

### Next steps for you

1) Implement files per plan (adapter + config + wiring) and add tests. Keep contracts untouched.
2) Ensure runner selection (echo vs SDK) works via env.
3) Validate with `just check` and the README quickstart.
4) Avoid secrets and keep toolset empty for now; tools will be added in a follow-up issue.
