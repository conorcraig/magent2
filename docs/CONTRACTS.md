# Contracts (v1)

These contracts are frozen for parallel work. Propose changes via a new
issue (suggest `v1.1`) with exact field/API diffs.

## Message envelope (v1)

Source: `magent2/models/envelope.py`

Fields:

- `id: str` (uuid) – auto generated
- `conversation_id: str` – routes messages and session state
- `sender: str` – e.g. `user:conor`, `agent:DevAgent`
- `recipient: str` – e.g. `agent:ReviewerAgent`
- `type: "message" | "control"`
- `content: str | None`
- `metadata: dict[str, Any]`
- `created_at: datetime` (UTC)

### Stream event shapes (v1)

- `TokenEvent`: `{ event: "token", conversation_id, text: str, index: int }`
- `ToolStepEvent`: `{ event: "tool_step", conversation_id, name: str, args:
  dict, result_summary?: str }`
- `OutputEvent`: `{ event: "output", conversation_id, text: str, usage?:
  dict }`

Note: the `event` discriminator is present on all three event models in code
(`TokenEvent`, `ToolStepEvent`, `OutputEvent`) and should be used by clients
to multiplex SSE or other streaming transports.

## Bus API (v1)

Source: `magent2/bus/interface.py`

Keep small and stable.

- `class BusMessage { id: str(uuid), topic: str, payload: dict }`
- `publish(topic: str, message: BusMessage) -> str`
- `read(topic: str, last_id: str | None, limit: int = 100) -> Iterable[
  BusMessage]`

Topic conventions:

- inbound: `chat:{conversation_id}` or `chat:{agent_name}`
- stream: `stream:{conversation_id}`
- control: `control:{agent_name}`

## Versioning

- No breaking changes to v1 this milestone.
- Additive changes bump to v1.1 and are documented here.
- Breaking changes require deprecation + adapter support.
