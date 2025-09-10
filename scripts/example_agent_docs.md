# OpenAI Agents SDK - Multi-Agent Streaming & Guardrails Demo

## Purpose of This Document

This document explains the architecture and design choices in the provided `multi_agent_streaming_guardrails_demo.py` example. It extracts key learnings from the official SDK docs and cookbooks so that engineers new to the SDK can read, understand, and safely extend the code without breaking it.

## Core Concepts

### 1. Agents

An Agent is the central unit of logic. It defines:

- Which model to call
- Instructions (system prompt)
- Which tools it can use
- Optional output schema (Pydantic)
- Optional guardrails or handoffs

Agents are lightweight definitions: they do not store conversation state.

### 2. Runner

The Runner executes an Agent. It:

- Prepares inputs and context
- Pulls conversation history from a Session (if provided)
- Calls the model
- Handles tool calls and handoffs
- Returns a RunResult object

### 3. Tools

Tools are Python functions annotated with decorators like `@function_tool`. They are exposed to the LLM as callable APIs. The Runner routes tool calls to these functions and feeds results back to the model.

### 4. Context

Context has two layers:

- **Local context**: Python objects (dataclasses, dicts, etc.) passed as `context` to `Runner.run`. Accessible in tools via `RunContextWrapper`. Not visible to the model.
- **Conversation context**: the message history seen by the model. Managed manually or automatically by Sessions.

### 5. Sessions (Memory)

Sessions store conversation history. Options include:

- `SQLiteSession` (local persistent file)
- `SQLAlchemySession` (database)
- `OpenAIConversationsSession` (hosted)

Sessions automatically prepend history before each run and append results afterward. Multiple agents can share a session to collaborate on the same conversation.

### 6. RunResult

Each run returns a RunResult with:

- `final_output` (structured output if defined)
- `items` (conversation items)
- `usage` (token usage metadata)

### 7. Multi-Agent Patterns

The demo shows two ways agents can collaborate:

- **Agents-as-tools**: one agent exposes another as a tool, allowing hub-and-spoke collaboration.
- **Handoffs**: one agent transfers control to another for the remainder of the conversation.

These patterns enable orchestration, delegation, and specialization.

### 8. WebSearch Tool

The SDK ships hosted tools like `WebSearchTool`, `FileSearchTool`, and `ComputerUse`. These are directly callable by agents and provide ready-made capabilities without writing custom tool functions.

### 9. Streaming

`Runner.run_streamed` yields events during execution. Event types include:

- `agent_updated_stream_event` (agent handoffs)
- `run_item_stream_event` (tool calls, outputs, messages)
- `raw_response_event` (low-level tokens)

This supports building progress UIs where users see tool calls, partial responses, and agent changes in real time.

### 10. Guardrails

Guardrails enforce policies:

- **Input guardrails**: check or block user input before the model runs.
- **Output guardrails**: check or block assistant output before returning it.

Guardrails are themselves agents that classify inputs/outputs and trigger tripwires if conditions are met.

### 11. Tracing

The SDK supports tracing for observability. You can:

- Use `trace(...)` to start a traced workflow
- Add `custom_span(...)` for sub-operations
- Inspect metadata for debugging or evaluation

Tracing can be disabled globally or per-run.

## How to Extend Safely

1. **Adding new tools**: Define with `@function_tool` and include them in an agent's tools list.
2. **Adding new agents**: Create specialized agents with their own instructions and tools. Decide whether to expose them via agents-as-tools or handoffs.
3. **Modifying context**: Extend `AppCtx` dataclass with new fields. Remember this is private state.
4. **Modifying memory**: Switch to `SQLAlchemySession` for multi-user scale or `OpenAIConversationsSession` for hosted storage.
5. **Adjusting output schemas**: Use Pydantic models to enforce structure. Ensure tools and instructions support producing this structure.
6. **Extending guardrails**: Write new guardrail agents to enforce domain-specific rules.
7. **UI integration**: Use `run_streamed` and consume StreamEvents to update your frontend incrementally.

## Common Pitfalls

- Do not put secrets into instructions; keep them in local context.
- Remember only conversation history is visible to the LLM.
- If you extend context, tools must explicitly access it; the model cannot.
- Guardrail tripwire exceptions must be caught and handled in your app.
- Streaming requires async iteration; forgetting to `await get_final_result` will drop the final output.

## References

OpenAI Agents SDK Docs:

- Overview: <https://openai.github.io/openai-agents-python/>
- Running Agents: <https://openai.github.io/openai-agents-python/running_agents/>
- Sessions: <https://openai.github.io/openai-agents-python/sessions/>
- Guardrails: <https://openai.github.io/openai-agents-python/guardrails/>
- Tracing: <https://openai.github.io/openai-agents-python/tracing/>
- Cookbooks: <https://github.com/openai/openai-agents-python/tree/main/cookbooks>

---

## Walkthrough of `scripts/example_agent.py`

### Local context (`AppCtx`)

- Private Python state passed via `context` to runs.
- Accessed by tools through `RunContextWrapper[AppCtx]`.

### Typed outputs (`Answer`, `TriagedTask`)

- Pydantic models for structured outputs; set on agents via `output_type`.

### Tools

- `@function_tool get_pref(w, key)` and `fetch_internal(w, query)` read from `AppCtx`.
- `add(a, b)` demonstrates a simple deterministic tool.

### Dynamic instructions

- `dyn_instructions(w, agent) -> str` builds system text at run time from context (tone/units).

### Specialist agents

- `research_agent`: uses `WebSearchTool()` + `fetch_internal`; returns `Answer`.
- `math_agent`: uses `add`; deterministic (temperature 0).
- `writer_agent`: uses `get_pref`; returns `Answer`.

### Orchestration

- Agents-as-tools: `research_agent.as_tool(...)`, `math_agent.as_tool(...)`, `writer_agent.as_tool(...)`.
- Handoff: `handoff(writer_agent)` for ownership transfer when heavy rewriting is needed.

### Guardrails

- Input: `no_homework` calls a classifier agent; trips if homework is detected.
- Output: `require_sources_if_web` checks web reliance implies non-empty sources.
- Exceptions handled: `InputGuardrailTripwireTriggered`, `OutputGuardrailTripwireTriggered`.

### Streaming & tracing

- `Runner.run_streamed(...)` with event handlers for tool calls, outputs, and agent updates.
- Tracing spans via `trace(...)` and `custom_span(...)` around the demo workflow.

### Sessions

- `SQLiteSession("thread-007", "multi_agent_history.db")` preserves conversation.
- Pass the session to `Runner.run_streamed(..., session=session)` for memory.

---

## Practical extensions

1. Add a retrieval tool (e.g., file search) and expose it to `research_agent`.
2. Add a `SQLAlchemySession` for multi-user/server deployments (if available).
3. Extend guardrails for domain policies (e.g., PII/PHI checks, compliance rules).
4. Capture and log usage metadata from `RunResult.usage` for analytics.

---

## Version notes & portability

- Some symbols vary by version (e.g., `RunContextWrapper`, persistent sessions). Guard optional imports.
- Prefer capability checks over strict type equality for stream event handling.
