You must search this docs reference page to find info, it is very extensive, has many useful examples

<https://openai.github.io/openai-agents-python/>

### Reference docs tree

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

### Mapping guidance (for magent2 v1 events)

- raw_response_event + `ResponseTextDeltaEvent.delta` → TokenEvent(text=delta, index++)
- run_item_stream_event (tool invocation) → ToolStepEvent(name=<tool>, args=<dict>)
- run_item_stream_event (tool result) → ToolStepEvent(name=<tool>, result_summary=<short>)
- final assistant message or end-of-stream → OutputEvent(text=<final_text>, usage=? if available)

### Sessions (conversation memory)

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

### Pitfalls

- Event shapes may evolve; prefer feature checks over strict type equality.
- Some SDK objects are dict-like and typed; try attribute access first, then `get`.
- If no explicit final output API is exposed by your version, accumulate token deltas and emit that as final output.
