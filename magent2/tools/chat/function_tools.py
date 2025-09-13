from __future__ import annotations

import os
from typing import Any

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import MessageEnvelope
from magent2.observability import (
    get_json_logger,
    get_metrics,
    get_run_context,
)

_TEST_BUS: Bus | None = None
_BUS_CACHE: Bus | None = None


def set_bus_for_testing(bus: Bus | None) -> None:
    global _TEST_BUS, _BUS_CACHE
    _TEST_BUS = bus
    # Keep cache in sync so tests can reset to a clean state
    if bus is None:
        _BUS_CACHE = None


def _get_bus() -> Bus:
    if _TEST_BUS is not None:
        return _TEST_BUS
    global _BUS_CACHE
    if _BUS_CACHE is not None:
        return _BUS_CACHE
    # Lazy import to avoid hard dependency in tests
    from magent2.bus.redis_adapter import RedisBus

    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _BUS_CACHE = RedisBus(url)
    return _BUS_CACHE


def _resolve_sender() -> str:
    name = os.getenv("AGENT_NAME", "").strip()
    return f"agent:{name}" if name else "agent:unknown"


def _resolve_conversation_id(
    recipient: str, ctx: dict[str, Any] | None, explicit_conversation_id: str | None
) -> str:
    if recipient.startswith("chat:"):
        cid = recipient.split(":", 1)[1]
        if cid:
            return cid
    # For agent recipients, precedence: explicit param > context > env
    if explicit_conversation_id is not None and explicit_conversation_id.strip():
        return explicit_conversation_id.strip()
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
    conversation_id: str, sender: str, recipient: str, content: str, metadata: dict[str, Any] | None
) -> MessageEnvelope:
    return MessageEnvelope(
        conversation_id=conversation_id,
        sender=sender,
        recipient=recipient,
        type="message",
        content=content,
        metadata=dict(metadata or {}),
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
    recipient: str,
    content: str,
    *,
    conversation_id: str | None = None,
    context: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger = get_json_logger("magent2.tools")
    metrics = get_metrics()
    ctx = get_run_context() or {}
    rec = (recipient or "").strip()
    if not rec or not (rec.startswith("chat:") or rec.startswith("agent:")):
        raise ValueError("recipient must be 'chat:{conversation_id}' or 'agent:{Name}'")
    text = (content or "").strip()
    if not text:
        raise ValueError("content must be non-empty")

    cid = _resolve_conversation_id(rec, context, conversation_id)
    sender = _resolve_sender()
    env = _build_envelope(cid, sender, rec, text, metadata)

    bus = _get_bus()
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool": "chat.send",
            "metadata": {"recipient": rec},
        },
    )
    metrics.increment(
        "tool_calls",
        {"tool": "chat", "conversation_id": str(ctx.get("conversation_id", ""))},
    )
    try:
        published_to = _publish(bus, env)
        return {"ok": True, "envelope_id": env.id, "published_to": published_to}
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tool error",
            extra={
                "event": "tool_error",
                "tool": "chat.send",
                "metadata": {"error": str(exc)[:200]},
            },
        )
        metrics.increment(
            "tool_errors", {"tool": "chat", "conversation_id": str(ctx.get("conversation_id", ""))}
        )
        raise
