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

- Imports (SDK): per docs examples, the package exposes `Agent`, `Runner`, and tool decorators from the top-level `agents` module. Token delta events come from `openai.types.responses`.
  - Required:
    - `from agents import Agent, Runner`
    - Optional tool decorator: `from agents import function_tool`
    - Token delta type for streaming: `from openai.types.responses import ResponseTextDeltaEvent`
- Do not introduce any changes to v1 contracts. Only add new code and wire-up.
- Keep runner mapping defensive: accept both typed objects and dict-shaped events; ignore unrecognized event types; log at debug level if needed (logging infra may land later; keep silent or minimal).
- Keep tool support minimal initially. If no tools are configured, the agent will operate as pure chat.

Naming note (avoid collisions): our codebase defines a local `Runner` Protocol in `magent2/worker/worker.py`. To prevent confusion with the SDK `Runner`, alias the import when implementing the adapter, e.g.:

```python
from agents import Runner as SDKRunner  # SDK class

# In adapter implementation, call SDKRunner.run_streamed(...)
```

### SDK usage cheatsheet (no web needed)

Use these snippets with `openai-agents>=0.2.11` (see `pyproject.toml`).

```python
from agents import Agent, Runner  # core SDK
from openai.types.responses import ResponseTextDeltaEvent  # token deltas

# 1) Construct an Agent (minimal chat-only)
agent = Agent(
    name="DevAgent",
    instructions="You are a helpful assistant.",
    # tools=[]  # keep empty for now; add later via function_tool-decorated functions
)

# 2) Streamed run (async) — yields SDK stream events
async def run_stream(input_text: str, session=None):
    rs = Runner.run_streamed(agent, input=input_text, session=session)
    async for ev in rs.stream_events():
        # ev.type commonly one of:
        # - "raw_response_event": carries low-level model events from OpenAI SDK
        # - "run_item_stream_event": high-level agent steps (tool calls/results, messages)
        # - "agent_updated_stream_event": agent handoffs/updates
        if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
            text_piece = ev.data.delta  # token chunk (str)
            # map → TokenEvent(text=text_piece)
        elif ev.type == "run_item_stream_event":
            data = ev.data  # may be typed or dict-like; prefer attribute access then keys
            # Heuristics:
            # - If it represents a tool invocation, expect fields like name/tool_name and arguments/args
            #   map → ToolStepEvent(name=..., args=...)
            # - If it represents a tool result/completion, prefer summarizing into result_summary
            # - If it represents an assistant/output message, capture text for OutputEvent at the end
        elif ev.type == "agent_updated_stream_event":
            # Non-essential for v1 mapping; can be ignored
            pass
    # Optionally, rs may expose a way to obtain final output; if unavailable, join accumulated tokens
    # final_text = await rs.get_final_output()  # if provided by the SDK version
    # map → OutputEvent(text=final_text)
```

Notes:

- If `rs.get_final_output()` (or similar) is unavailable, accumulate token deltas during iteration and emit a single `OutputEvent` at the end with the joined text.
- If tools are configured, prefer emitting `ToolStepEvent` at invocation time (with args) and another at completion with `result_summary` (short string).

### Event mapping table (SDK → v1)

- **raw_response_event + ResponseTextDeltaEvent(delta: str)** → `TokenEvent(text=delta, index=++)`
- **run_item_stream_event (tool invocation)** → `ToolStepEvent(name=<tool>, args=<dict>)`
- **run_item_stream_event (tool result)** → `ToolStepEvent(name=<tool>, result_summary=<short>)`
- **assistant/output message end (or stream completion)** → `OutputEvent(text=<final_text>, usage=? if available)`

### Session hints (SDK)

- Sessions preserve conversation history. If a concrete `Session` class is available in your installed SDK version, prefer passing it to `Runner.run_streamed(..., session=...)`.
- Example path for a persistent session (check your installed package):
  - `from agents.extensions.memory.sqlalchemy_session import SQLAlchemySession`  # if available
  - Otherwise, keep an in-memory map of session-like objects or pass `None` and rely on stateless runs for now. Our adapter will still maintain per-`conversation_id` state if needed.

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
        # finally, enqueue OutputEvent either when a final-output event is observed
        # or by joining accumulated token text at stream end as a fallback
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
