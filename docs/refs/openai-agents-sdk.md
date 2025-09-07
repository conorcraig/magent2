# OpenAI Agents SDK – integration notes

- Sessions: keep one SDK session per `conversation_id`. Store session handle and reuse for subsequent turns.
- Event mapping: map streamed partials to `TokenEvent`; summarize tool calls to `ToolStepEvent` (name, args summary, result summary); final answer to `OutputEvent`.
- Tools: wrap local capabilities (Terminal, Todo, MCP) as SDK function tools with explicit, validated schemas. Keep side‑effects idempotent.
- Handoffs: model multi‑agent flows explicitly (e.g., Triage → Specialist). Use addressing + Bus for cross‑agent chat when needed.
- Fallback: when `OPENAI_API_KEY` is absent, use a local echo runner so Worker remains operable for E2E.

## Example (basic agent run – sync)

```python
from agents import Agent, Runner

agent = Agent(name="DevAgent", instructions="Reply concisely.")
res = Runner.run_sync(agent, "Write a haiku about recursion.")
print(res.final_output)
```

## Example (Worker mapping – conceptual)

```python
for sdk_event in runner.stream_run_sdk(envelope):
    if sdk_event.type == "token":
        yield TokenEvent(conversation_id=cid, text=sdk_event.text, index=idx)
    elif sdk_event.type == "tool":
        yield ToolStepEvent(
            conversation_id=cid,
            name=sdk_event.name,
            args=sdk_event.args,
            result_summary=sdk_event.result_summary,
        )
    elif sdk_event.type == "final":
        yield OutputEvent(conversation_id=cid, text=sdk_event.text, usage=sdk_event.usage)
```

## Checklist
- [ ] Session store keyed by `conversation_id`
- [ ] Streamed partials surfaced promptly to SSE
- [ ] Tool schemas strict; reject invalid input early
- [ ] Backoff/retry for transient API errors; clear error events
- [ ] Redact secrets from logs/events

## References
- OpenAI Agents SDK docs; platform rate limits; function calling guide
