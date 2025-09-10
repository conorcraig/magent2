# OpenAI Agents SDK — FAQ

## What is an Agent vs. a Runner?

- **Agent**: configuration (instructions, tools, model, guardrails, output schema). Stateless by itself.
- **Runner**: executes an Agent with inputs, optional session, and context; handles tools, handoffs, and streaming.

## When should I use agents-as-tools vs. handoffs?

- **Agents-as-tools**: keep control in a central orchestrator; call specialists when needed.
- **Handoffs**: transfer ownership to a specialist for the remainder of a task or conversation segment.

## How do I persist conversation history?

- Use a Session (e.g., `SQLiteSession`). Pass the same session to all runs/agents that collaborate on the same thread.

## How do I enforce structured outputs?

- Provide a Pydantic model via `output_type` on the agent. The Runner validates and returns that structure.

## How do I pass private state to tools?

- Use `RunContextWrapper` to access local Python objects (`ctx.context`). Do not embed secrets in `instructions`.

## How do I block certain inputs/outputs?

- Implement input/output guardrails and raise tripwires by returning `GuardrailFunctionOutput(..., tripwire_triggered=True)`.

## How do I show real-time progress in a UI?

- Use `Runner.run_streamed` and iterate `stream_events()` for token deltas, tool steps, and handoffs.

## Can I mix providers?

- Some versions support LiteLLM-based providers. See `/ref/extensions/models/` in upstream docs.

## Is Computer Use or Voice supported?

- See `/ref/computer/` and `/ref/voice/` in the upstream docs. Availability varies by version and account access.

## Debugging tips?

- Enable tracing with `trace(...)` spans.
- Log `run.items` during development to see tool flow.
- Catch guardrail exceptions to differentiate policy vs. runtime errors.
