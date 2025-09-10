# OpenAI Agents SDK — Local Reference Index

You must search this docs reference page to find info, it is very extensive, has many useful examples

<https://openai.github.io/openai-agents-python/>

## Reference docs tree

Links are relative to `https://openai.github.io/openai-agents-python/`.

├── [agent](/ref/agent/)
├── [agent_output](/ref/agent_output/)
├── [computer](/ref/computer/)
├── [exceptions](/ref/exceptions/)
├── [extensions](/ref/extensions/)
│   ├── [handoff_filters](/ref/extensions/handoff_filters/)
│   ├── [handoff_prompt](/ref/extensions/handoff_prompt/)
│   ├── [litellm](/ref/extensions/litellm/)
│   ├── [memory](/ref/extensions/memory/)
│   │   └── [sqlalchemy_session](/ref/extensions/memory/sqlalchemy_session/)
│   ├── [models](/ref/extensions/models/)
│   │   ├── [litellm_model](/ref/extensions/models/litellm_model/)
│   │   └── [litellm_provider](/ref/extensions/models/litellm_provider/)
│   └── [visualization](/ref/extensions/visualization/)
├── [function_schema](/ref/function_schema/)
├── [guardrail](/ref/guardrail/)
├── [handoffs](/ref/handoffs/)
├── [items](/ref/items/)
├── [lifecycle](/ref/lifecycle/)
├── [logger](/ref/logger/)
├── [mcp](/ref/mcp/)
│   ├── [server](/ref/mcp/server/)
│   └── [util](/ref/mcp/util/)
├── [memory](/ref/memory/)
├── [model_settings](/ref/model_settings/)
├── [models](/ref/models/)
│   ├── [chatcmpl_converter](/ref/models/chatcmpl_converter/)
│   ├── [chatcmpl_helpers](/ref/models/chatcmpl_helpers/)
│   ├── [chatcmpl_stream_handler](/ref/models/chatcmpl_stream_handler/)
│   ├── [default_models](/ref/models/default_models/)
│   ├── [fake_id](/ref/models/fake_id/)
│   ├── [interface](/ref/models/interface/)
│   ├── [multi_provider](/ref/models/multi_provider/)
│   ├── [openai_chatcompletions](/ref/models/openai_chatcompletions/)
│   ├── [openai_provider](/ref/models/openai_provider/)
│   └── [openai_responses](/ref/models/openai_responses/)
├── [prompts](/ref/prompts/)
├── [realtime](/ref/realtime/)
│   ├── [agent](/ref/realtime/agent/)
│   ├── [config](/ref/realtime/config/)
│   ├── [events](/ref/realtime/events/)
│   ├── [handoffs](/ref/realtime/handoffs/)
│   ├── [items](/ref/realtime/items/)
│   ├── [model](/ref/realtime/model/)
│   ├── [model_events](/ref/realtime/model_events/)
│   ├── [model_inputs](/ref/realtime/model_inputs/)
│   ├── [openai_realtime](/ref/realtime/openai_realtime/)
│   ├── [runner](/ref/realtime/runner/)
│   └── [session](/ref/realtime/session/)
├── [repl](/ref/repl/)
├── [result](/ref/result/)
├── [run](/ref/run/)
├── [run_context](/ref/run_context/)
├── [stream_events](/ref/stream_events/)
├── [strict_schema](/ref/strict_schema/)
├── [tool](/ref/tool/)
├── [tool_context](/ref/tool_context/)
├── [tracing](/ref/tracing/)
│   ├── [create](/ref/tracing/create/)
│   ├── [logger](/ref/tracing/logger/)
│   ├── [processor_interface](/ref/tracing/processor_interface/)
│   ├── [processors](/ref/tracing/processors/)
│   ├── [provider](/ref/tracing/provider/)
│   ├── [scope](/ref/tracing/scope/)
│   ├── [setup](/ref/tracing/setup/)
│   ├── [span_data](/ref/tracing/span_data/)
│   ├── [spans](/ref/tracing/spans/)
│   ├── [traces](/ref/tracing/traces/)
│   └── [util](/ref/tracing/util/)
├── [usage](/ref/usage/)
├── [version](/ref/version/)
└── [voice](/ref/voice/)
    ├── [events](/ref/voice/events/)
    ├── [exceptions](/ref/voice/exceptions/)
    ├── [imports](/ref/voice/imports/)
    ├── [input](/ref/voice/input/)
    ├── [model](/ref/voice/model/)
    ├── [models](/ref/voice/models/)
    │   ├── [openai_model_provider](/ref/voice/models/openai_model_provider/)
    │   ├── [openai_provider](/ref/voice/models/openai_provider/)
    │   ├── [openai_stt](/ref/voice/models/openai_stt/)
    │   └── [openai_tts](/ref/voice/models/openai_tts/)
    ├── [pipeline](/ref/voice/pipeline/)
    ├── [pipeline_config](/ref/voice/pipeline_config/)
    ├── [result](/ref/voice/result/)
    ├── [utils](/ref/voice/utils/)
    └── [workflow](/ref/voice/workflow/)

---

## Offline cheatsheet (Python SDK)

Works with `openai-agents>=0.2.11` (see `pyproject.toml`). These are the concrete imports and patterns we use in magent2.

### Imports

```python
from agents import Agent, Runner
# Optional tool decorator
from agents import function_tool

# Token delta events for streaming
from openai.types.responses import ResponseTextDeltaEvent
```

### Minimal agent (chat-only)

```python
from agents import Agent, Runner

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
)

# Sync call (non-streaming)
result = Runner.run_sync(agent, "Say hi")
print(result.final_output)  # string
```

### Streaming events (async)

```python
import asyncio
from agents import Agent, Runner
from openai.types.responses import ResponseTextDeltaEvent

async def main() -> None:
    agent = Agent(name="Assistant", instructions="Be concise.")
    rs = Runner.run_streamed(agent, input="Tell me a joke")

    async for ev in rs.stream_events():
        # Common types (string identifiers on ev.type):
        # - "raw_response_event" → low-level model deltas (OpenAI Responses API)
        # - "run_item_stream_event" → agent steps (tool calls/results, messages)
        # - "agent_updated_stream_event" → agent handoff/updates (often ignorable)
        if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
            print(ev.data.delta, end="", flush=True)  # token chunk
        elif ev.type == "run_item_stream_event":
            data = ev.data  # may be typed object or dict-like
            # Heuristics:
            # - tool invocation → has tool name + arguments
            # - tool result → contains result/output; summarize
            # - assistant message → accumulate for final output

asyncio.run(main())
```

## Mapping guidance (for magent2 v1 events)

- raw_response_event + `ResponseTextDeltaEvent.delta` → TokenEvent(text=delta, index++)
- run_item_stream_event (tool invocation) → ToolStepEvent(name=`tool`, args=`dict`)
- run_item_stream_event (tool result) → ToolStepEvent(name=`tool`, result_summary=`short`)
- final assistant message or end-of-stream → OutputEvent(text=<final_text>, usage=? if available)

## Sessions (conversation memory)

- Pass a session object to preserve history across runs: `Runner.run_streamed(agent, input=..., session=session)`.
- If available in your installed version, a persistent session can be imported as:

```python
from agents.extensions.memory.sqlalchemy_session import SQLAlchemySession  # if present
session = SQLAlchemySession("sqlite:///./agents.db", key="conv_123")
```

- If unavailable, keep an in-memory dict keyed by `conversation_id` and reuse the same object (or `None`) consistently.

### Tools (optional)

```python
from agents import Agent, Runner, function_tool

@function_tool
def add(a: int, b: int) -> int:
    return a + b

agent = Agent(name="MathAgent", instructions="Use tools when helpful.", tools=[add])
result = Runner.run_sync(agent, "What is 2+3?")
```

### Function tools quick reference (naming, schema, returns)

- Decorator: `@function_tool` or `@function_tool(name="...", description="...")`.
- Input schema is inferred from Python type hints on parameters.
- Return values must be JSON-serializable (basic types, dicts/lists).
- Use precise typing (`dict[str, Any]`, `list[dict[str, Any]]`) for good schemas.
- Prefer wrapping objects in dicts, e.g., `{ "task": { ... } }` or `{ "ok": true }`.
- For bad input, raise `ValueError` with a concise message; the SDK surfaces it.
- For transient backend errors, consider returning `{ "error": "...", "transient": true }`.

Example:

```python
from __future__ import annotations
from typing import Any
from agents import function_tool

@function_tool(name="todo_create")
def create_task(conversation_id: str, title: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not conversation_id or not conversation_id.strip():
        raise ValueError("conversation_id must be non-empty")
    if not title or not title.strip():
        raise ValueError("title must be non-empty")
    # ... call your store and return a JSON-safe dict
    return {"task": {"id": "example"}}
```

### Pitfalls

- Event shapes may evolve; prefer feature checks over strict type equality.
- Some SDK objects are dict-like and typed; try attribute access first, then `get`.
- If no explicit final output API is exposed by your version, accumulate token deltas and emit that as final output.

### Tool context (RunContextWrapper) — dependency injection

Some SDK versions expose a run/tool context wrapper to pass shared state (e.g., DB handles, user/session info) to tools.

```python
from __future__ import annotations
from typing import Any
from agents import function_tool, RunContextWrapper  # verify availability in your version

class MyContext:
    def __init__(self) -> None:
        self.request_id = "req-123"
        self.user = {"name": "alice"}

@function_tool
def get_request_info(ctx: RunContextWrapper[Any]) -> dict[str, Any]:
    """Return request/user info from the run context."""
    # Access the underlying context object
    c = getattr(ctx, "context", None)
    return {
        "request_id": getattr(c, "request_id", None),
        "user": getattr(c, "user", None),
    }
```

Notes:

- If `RunContextWrapper` is not present in your installed SDK version, omit the context parameter and read from environment/config instead.
- For magent2 chat tools, prefer passing `conversation_id` via context when available.

### Tool error handling via decorator

The decorator can transform exceptions into a tool return using a failure handler, keeping the run alive and returning a compact error string:

```python
from agents import function_tool

def _tool_error(e: Exception) -> str:
    # Keep short to fit model context; avoid leaking internal details
    return f"error: {type(e).__name__}: {e}"[:200]

@function_tool(failure_error_function=_tool_error)
def safe_echo(text: str) -> str:
    if not text.strip():
        raise ValueError("text must be non-empty")
    return text
```

Tip:

- Prefer raising `ValueError` for input validation issues so the SDK surfaces a clear tool error.

---

## API quick reference (Python)

### Agent

- Constructor fields (common):
  - `name: str`
  - `instructions: str | Callable[[RunContextWrapper, AgentBase], str]`
  - `model: str` (e.g., `"gpt-4o-mini"`)
  - `tools: list[Callable]` (decorated with `@function_tool` or `agent.as_tool(...)`)
  - `handoffs: list[Agent]` (use `handoff(other_agent)` when needed)
  - `output_type: type[BaseModel] | None` (Pydantic for structured output)
  - `model_settings: ModelSettings` (e.g., `temperature: float`)
  - `input_guardrails: list[Callable]`, `output_guardrails: list[Callable]`

### Runner

- `Runner.run_sync(agent, input, context=None, session=None, run_config=None)`
- `await Runner.run(agent, input, context=None, session=None, run_config=None)`
- `Runner.run_streamed(agent, input, context=None, session=None, run_config=None)` → returns a streamed run handle with `stream_events()` and `get_final_result()`

### Sessions

- `SQLiteSession(key: str, path: str)`
- `SQLAlchemySession(url: str, key: str)` (if available in your version)
- `OpenAIConversationsSession(key: str)` (if available/installed)

Notes:

- Sessions prepend history and append new items automatically.
- Reuse the same session instance (by `key`) for multi-agent collaboration.

### ModelSettings (common)

- `temperature: float = 0.0..1.0`
- Optional: `max_output_tokens`, `stop`, provider-specific options (check your installed SDK version).

---

## Event taxonomy (streaming)

Common `ev.type` values when iterating `run_streamed(...).stream_events()`:

- `raw_response_event`
  - Low-level token deltas from the underlying Responses API.
  - Often typed as `ResponseTextDeltaEvent` (Python import shown above).
- `run_item_stream_event`
  - High-level agent steps. The `ev.data`/`ev.item` typically represents one of:
    - tool invocation (name + args)
    - tool result (result or a summarized output)
    - assistant message chunk or final assistant message
- `agent_updated_stream_event`
  - Indicates an agent handoff or agent definition change in-flight.

Mapping guidance for local UIs:

- Token stream → accumulate `raw_response_event` text deltas for typing effect.
- Tool steps → render name + compact args, then summarize result on completion.
- Final output → use `await streamed.get_final_result()` for structured `final_output`.

---

## Hosted tools cheat sheet

- `WebSearchTool()`
  - Quick access to web search/results (Responses-model hosted capability).
  - Ensure outputs include citations when your policies require it.
- `FileSearchTool()`
  - Index/search local or remote files (check version/docs for setup needs).
- `ComputerUse`
  - Programmatic computer interaction (advanced; verify availability and safety settings).

Add hosted tools to `Agent(tools=[...])` like any function tool. Keep inputs/outputs simple and JSON-serializable.

---

## Guardrails quick recipes

### Input guardrail

```python
from agents import input_guardrail, GuardrailFunctionOutput, Runner

@input_guardrail
async def block_pii(w, agent, input):
    # Call a classifier agent or do a fast heuristic
    # Return tripwire_triggered=True to block
    flagged = False
    return GuardrailFunctionOutput(output_info={"flagged": flagged}, tripwire_triggered=flagged)
```

### Output guardrail

```python
from agents import output_guardrail, GuardrailFunctionOutput

@output_guardrail
async def require_sources(w, agent, output):
    has_sources = bool(getattr(output, "sources", None))
    return GuardrailFunctionOutput(output_info={"has_sources": has_sources}, tripwire_triggered=not has_sources)
```

Exceptions to catch around runs:

- `InputGuardrailTripwireTriggered`
- `OutputGuardrailTripwireTriggered`

---

## Sessions matrix (when to use what)

- Local dev / single-user: `SQLiteSession(key, path)`
- Multi-user / server: `SQLAlchemySession(db_url, key)` (if supported)
- Hosted conversations: `OpenAIConversationsSession(key)` (if supported)

Operational tips:

- Use stable `key` per conversation/thread.
- Share sessions across collaborating agents for coherent history.

---

## Tracing & spans

```python
from agents import trace, custom_span, RunConfig, Runner

with trace("MyFlow", metadata={"tenant": "acme"}):
    with custom_span("loading"):
        pass
    result = Runner.run_sync(agent, "hello")
```

Per-run config (disable tracing or sensitive data):

```python
run_config = RunConfig(tracing_disabled=False, trace_include_sensitive_data=False)
Runner.run_streamed(agent, input="...", run_config=run_config)
```

---

## MCP, realtime, and voice

- MCP (Model Context Protocol): run external tools/providers via MCP servers (see `/ref/mcp/`).
- Realtime & Voice: see `/ref/realtime/*` and `/ref/voice/*` for events, models, and pipelines.

Notes:

- Event shapes and capabilities can vary by version; prefer feature checks over strict type matches.

---

## Version notes

- This reference assumes `openai-agents>=0.2.11`.
- Some symbols (e.g., `RunContextWrapper`, `SQLAlchemySession`) may not exist in older installs; guard imports.
- When in doubt, consult the upstream docs tree linked at the top and adapt examples to your installed version.
