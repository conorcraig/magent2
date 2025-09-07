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
    name = os.getenv("AGENT_NAME", "").strip()
    return f"agent:{name}" if name else "agent:unknown"


def _resolve_conversation_id(
    recipient: str, ctx: dict[str, Any] | None
) -> str:
    if recipient.startswith("chat:"):
        cid = recipient.split(":", 1)[1]
        if cid:
            return cid
    # For agent recipients, try context/env
    if ctx is not None:
        value = ctx.get("conversation_id")
        cid_ctx = str(value).strip() if value is not None else ""
        if cid_ctx:
            return cid_ctx
    cid_env = os.getenv("CHAT_TOOL_CONVERSATION_ID", "").strip()
    if cid_env:
        return cid_env
    raise ValueError("conversation_id not available for agent recipient")


def _build_envelope(
    conversation_id: str, sender: str, recipient: str, content: str
) -> MessageEnvelope:
    return MessageEnvelope(
        conversation_id=conversation_id,
        sender=sender,
        recipient=recipient,
        type="message",
        content=content,
        metadata={},
    )


def _publish(bus: Bus, envelope: MessageEnvelope) -> list[str]:
    payload = envelope.model_dump(mode="json")
    topics: list[str] = []

    conv_topic = f"chat:{envelope.conversation_id}"
    bus.publish(conv_topic, BusMessage(topic=conv_topic, payload=payload))
    topics.append(conv_topic)

    if envelope.recipient.startswith("agent:"):
        agent_name = envelope.recipient.split(":", 1)[1]
        if agent_name:
            agent_topic = f"chat:{agent_name}"
            bus.publish(agent_topic, BusMessage(topic=agent_topic, payload=payload))
            topics.append(agent_topic)

    return topics


def send_message(
    recipient: str, content: str, *, context: dict[str, Any] | None = None
) -> dict[str, Any]:
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
    published_to = _publish(bus, env)
    return {"ok": True, "envelope_id": env.id, "published_to": published_to}

