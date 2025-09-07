# Handover: Chat tool as Agents SDK function tool for Bus (#37)

## Context

- Goal: Build a function tool that publishes addressed `MessageEnvelope` to the Bus for inter-agent messaging.
- Scope: Add a chat tool under `magent2/tools/chat/` and tests under `tests/`. Do not change frozen contracts.
- Contracts v1 (frozen):
  - Envelope: `magent2/models/envelope.py` (`MessageEnvelope`)
  - Bus API: `magent2/bus/interface.py` (`Bus`, `BusMessage`)
- Topic conventions (v1): inbound `chat:{conversation_id}` or `chat:{agent_name}`, stream `stream:{conversation_id}`.
- References: see `docs/CONTRACTS.md` and offline OpenAI Agents SDK cheatsheet in `docs/refs/openai-agents-sdk.md`.

## Deliverables

- An Agents SDK function tool named `chat_send` that accepts `recipient` and `content`, validates addressing, constructs a canonical `MessageEnvelope`, and publishes to the correct Bus topic(s).
  - Enhancement: optional `conversation_id` parameter (highest precedence) to avoid relying on env/context for `agent:{Name}` addressing.
- Tests using an `InMemoryBus` test double (pattern from `tests/test_bus_interface.py`) to validate publish behavior and payload shape.
- No changes to frozen v1 contracts; only new files under `magent2/tools/chat/` and new tests.

## Requirements and behavior

- Inputs (tool surface):
  - `recipient: str` — one of:
    - `chat:{conversation_id}` (direct to conversation)
    - `agent:{AgentName}` (address a specific agent)
  - `content: str` — message body (non-empty after trim)
  - `conversation_id: str | None` — optional explicit conversation id used when `recipient` is `agent:{Name}`; precedence over context/env.
- Validation:
  - Reject empty/whitespace `content`.
  - `recipient` must start with `chat:` or `agent:` and include a non-empty suffix.
  - For `agent:` recipients, a `conversation_id` must be known (see below) to publish to the conversation topic.
- Envelope:
  - Construct `MessageEnvelope` with fields: `id` (auto), `conversation_id`, `sender`, `recipient`, `type="message"`, `content`, `metadata={}` (or small context), `created_at` (UTC ISO string via `model_dump(mode="json")`).
- Publishing logic:
  - Always publish to conversation topic: `chat:{conversation_id}`.
  - If `recipient` starts with `agent:`, additionally publish to agent topic: `chat:{AgentName}`.
  - Return a small dict: `{ "ok": true, "envelope_id": str, "published_to": [topics...] }`.

## Conversation and sender resolution

- `conversation_id`:
  - If `recipient` starts with `chat:` → derive `conversation_id` from recipient suffix.
  - If `recipient` starts with `agent:` → resolve in precedence order: explicit parameter > Agents SDK run context (if available) > environment variable `CHAT_TOOL_CONVERSATION_ID`. If none provide a value, raise `ValueError("conversation_id not available for agent recipient")`.
- `sender`:
  - Prefer `sender = f"agent:{AGENT_NAME}"` with `AGENT_NAME` read from environment (already used by `worker` and `compose`). Fallback to `agent:unknown` if unset (or consider raising for stricter policy).

## Bus access and testability

- Provide a module-level `get_bus()` that returns a `Bus`:
  - Default: lazily construct and cache a single `RedisBus` using `REDIS_URL` (matches README/compose).
  - Tests: expose `set_bus_for_testing(bus: Bus)` to inject an `InMemoryBus` and reset cache when set to `None`.
- Wrap published payload with `BusMessage(topic=..., payload=<envelope_json>)`.

## File layout (new)

- `magent2/tools/chat/__init__.py` — export `chat_send` (decorated) and `send_message` (undecorated helper for unit tests).
-- `magent2/tools/chat/function_tools.py` — implementation:
  - `_resolve_conversation_id(recipient: str, ctx: dict | None, explicit_conversation_id: str | None) -> str`
  - `_resolve_sender() -> str`
  - `_build_envelope(conversation_id: str, sender: str, recipient: str, content: str) -> MessageEnvelope`
  - `_publish(bus: Bus, envelope: MessageEnvelope) -> list[str]` (returns list of topics)
  - `send_message(recipient: str, content: str, *, conversation_id: str | None = None, context: dict | None = None) -> dict` — pure function used by tests
  - Decorated tool `chat_send(recipient: str, content: str, conversation_id: str | None = None) -> dict` that calls `send_message(...)` and returns its dict
  - Bus injection helpers: `_get_bus()`, `set_bus_for_testing(...)`

## SDK usage (offline)

- The function tool decorator is available via the Agents SDK as shown in `docs/refs/openai-agents-sdk.md`:

```python
from agents import function_tool

@function_tool
def chat_send(recipient: str, content: str) -> dict:
    """Send a chat message to a conversation or agent via Bus.

    Args:
        recipient: "chat:{conversation_id}" or "agent:{AgentName}".
        content: Non-empty message text.

    Returns:
        {"ok": bool, "envelope_id": str, "published_to": list[str]}
    """
    # Delegate to undecorated implementation to keep this thin/testable
    from magent2.tools.chat.function_tools import send_message as _impl
    return _impl(recipient, content)
```

Notes:

- If your installed SDK version differs, confirm the import (`from agents import function_tool`) in `docs/refs/openai-agents-sdk.md`.
- If a tool/run context is available (e.g., `tool_context` or `run_context`), pass it through to `send_message(..., context=...)` so it can derive `conversation_id` when `recipient` is `agent:*`.

## Tests (TDD)

- Add `tests/test_chat_function_tool.py`:
  - Define `InMemoryBus` (copy pattern from `tests/test_bus_interface.py`).
  - Fixture `bus()` returns a fresh `InMemoryBus` and injects it via `set_bus_for_testing(bus)`.
  - Case: `recipient = "chat:conv1"` publishes exactly one message to topic `chat:conv1` with `payload["conversation_id"] == "conv1"`, `payload["recipient"] == "chat:conv1"`, `payload["type"] == "message"`, and `created_at` is a string.
  - Case: `recipient = "agent:DevAgent"`, with `CHAT_TOOL_CONVERSATION_ID=conv2` set in env, publishes to both `chat:conv2` and `chat:DevAgent`.
  - Case: invalid recipient (e.g., `"room:123"` or `"agent:"`) raises `ValueError`.
  - Case: empty/blank content raises `ValueError`.
- Keep tests deterministic; avoid Redis/network; no Agents SDK import is needed for unit tests (test undecorated `send_message`).

## Implementation sketch (pure helper)

```python
from __future__ import annotations
import os
from typing import Any
from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import MessageEnvelope

_TEST_BUS: Bus | None = None

def set_bus_for_testing(bus: Bus | None) -> None:
    global _TEST_BUS
    _TEST_BUS = bus

def _get_bus() -> Bus:
    if _TEST_BUS is not None:
        return _TEST_BUS
    # Lazy import to avoid hard dependency in tests
    from magent2.bus.redis_adapter import RedisBus  # type: ignore
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return RedisBus(url)

def _resolve_sender() -> str:
    name = os.getenv("AGENT_NAME", "")
    return f"agent:{name}" if name else "agent:unknown"

def _resolve_conversation_id(recipient: str, ctx: dict[str, Any] | None) -> str:
    if recipient.startswith("chat:"):
        cid = recipient.split(":", 1)[1]
        if cid:
            return cid
    # For agent recipients, try context/env
    if ctx is not None:
        cid_ctx = str(ctx.get("conversation_id") or "").strip()
        if cid_ctx:
            return cid_ctx
    cid_env = os.getenv("CHAT_TOOL_CONVERSATION_ID", "").strip()
    if cid_env:
        return cid_env
    raise ValueError("conversation_id not available for agent recipient")

def _build_envelope(conversation_id: str, sender: str, recipient: str, content: str) -> MessageEnvelope:
    return MessageEnvelope(
        conversation_id=conversation_id,
        sender=sender,
        recipient=recipient,
        type="message",
        content=content,
        metadata={},
    )

def send_message(recipient: str, content: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = (recipient or "").strip()
    if not rec or not (rec.startswith("chat:") or rec.startswith("agent:")):
        raise ValueError("recipient must be 'chat:{conversation_id}' or 'agent:{Name}'")
    text = (content or "").strip()
    if not text:
        raise ValueError("content must be non-empty")

    conversation_id = _resolve_conversation_id(rec, context)
    sender = _resolve_sender()
    env = _build_envelope(conversation_id, sender, rec, text)

    bus = _get_bus()
    payload = env.model_dump(mode="json")
    topics: list[str] = []

    conv_topic = f"chat:{conversation_id}"
    bus.publish(conv_topic, BusMessage(topic=conv_topic, payload=payload))
    topics.append(conv_topic)

    if rec.startswith("agent:"):
        agent_name = rec.split(":", 1)[1]
        if agent_name:
            agent_topic = f"chat:{agent_name}"
            bus.publish(agent_topic, BusMessage(topic=agent_topic, payload=payload))
            topics.append(agent_topic)

    return {"ok": True, "envelope_id": env.id, "published_to": topics}
```

## Risks and mitigations

- SDK context API differences: keep the undecorated `send_message` independent of SDK; the decorated `chat_send` can optionally fetch context and pass it in.
- Addressing drift: centralize validation in one place; raise clear errors.
- Redis dependency: default to `RedisBus` only outside tests; provide injection hook for tests.

## Validation

- Run locally: `just check` (format, lint, types, complexity, secrets, tests).
- Ensure tests pass on CI; do not run Redis in these unit tests (use `InMemoryBus`).

## Next steps for implementer

1) Create `magent2/tools/chat/function_tools.py` and `__init__.py` per layout above.
2) Add `tests/test_chat_function_tool.py` with fixtures and cases.
3) Wire the decorated `chat_send` thin wrapper (import path per `docs/refs/openai-agents-sdk.md`).
4) Run `just check`; fix any lint/type/test issues; open PR referencing #37.
