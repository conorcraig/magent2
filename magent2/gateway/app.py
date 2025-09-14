from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from magent2.bus.interface import Bus, BusMessage
from magent2.observability import configure_uvicorn_logging, get_json_logger, get_metrics


# ----------------------------
# SSE utilities
# ----------------------------
def _sse_cap_bytes() -> int | None:
    raw = os.getenv("GATEWAY_SSE_MAX_BYTES", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _truncate_payload_for_sse(payload: dict[str, Any], cap_bytes: int | None) -> dict[str, Any]:
    """Ensure a JSON-serializable payload fits within cap_bytes when encoded.

    If cap is None, return the payload as-is. If payload is too large, attempt
    to truncate the `text` field when present; otherwise emit a minimal
    truncated payload conserving the original event kind.
    """
    if cap_bytes is None:
        return payload

    # Quick fit check
    try:
        s = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(s) <= cap_bytes:
            return payload
    except Exception:
        return _create_minimal_truncated_payload(payload, cap_bytes)

    # Truncate `text` if present
    if isinstance(payload.get("text"), str):
        truncated = _truncate_text_field(payload, cap_bytes)
        if truncated:
            return truncated

    # Fallback to minimal payload
    return _create_minimal_truncated_payload(payload, cap_bytes)


def _truncate_text_field(payload: dict[str, Any], cap_bytes: int) -> dict[str, Any] | None:
    """Try to truncate the `text` field so the JSON fits within cap_bytes."""
    try:
        result = dict(payload)
        original_text = result["text"]

        base = dict(result)
        base["text"] = ""
        base["truncated"] = True
        base["cap_bytes"] = cap_bytes

        overhead = len(json.dumps(base, separators=(",", ":")).encode("utf-8"))
        allowed_text_bytes = max(0, cap_bytes - overhead)

        text_bytes = original_text.encode("utf-8")
        trimmed = text_bytes[:allowed_text_bytes].decode("utf-8", errors="ignore")
        base["text"] = trimmed

        if len(json.dumps(base, separators=(",", ":")).encode("utf-8")) <= cap_bytes:
            return base
    except Exception:
        pass
    return None


def _create_minimal_truncated_payload(payload: dict[str, Any], cap_bytes: int) -> dict[str, Any]:
    """Create a compact truncated payload that fits within cap_bytes."""
    try:
        event_type = "output"
        if isinstance(payload, dict) and "event" in payload:
            event_type = str(payload["event"])

        minimal = {
            "event": event_type,
            "truncated": True,
            "cap_bytes": cap_bytes,
        }

        minimal_json = json.dumps(minimal, separators=(",", ":")).encode("utf-8")
        if len(minimal_json) <= cap_bytes:
            return minimal
    except Exception:
        pass
    return {"event": "truncated"}


class SendRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender: str
    recipient: str
    type: Literal["message"] = "message"
    content: str


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()
    # Configure uvicorn logging at app startup to avoid import-time side effects
    configure_uvicorn_logging()
    logger = get_json_logger("magent2.gateway")
    metrics = get_metrics()

    @app.get("/health")
    async def health() -> dict[str, str]:  # lightweight healthcheck endpoint
        return {"status": "ok"}

    @app.post("/send")
    async def send(message: SendRequest) -> dict[str, Any]:
        # Always publish to conversation topic for compatibility
        payload = message.model_dump(mode="json")
        conv_topic = f"chat:{message.conversation_id}"
        try:
            bus.publish(conv_topic, BusMessage(topic=conv_topic, payload=payload))
        except Exception as exc:
            logger.error(
                "gateway send error",
                extra={
                    "event": "gateway_error",
                    "path": "send",
                    "conversation_id": message.conversation_id,
                },
            )
            raise HTTPException(status_code=503, detail="bus publish failed") from exc

        # Additionally publish to agent topic when recipient hints an agent
        recipient = str(message.recipient)
        if recipient.startswith("agent:"):
            agent_name = recipient.split(":", 1)[1] or ""
            if agent_name:
                agent_topic = f"chat:{agent_name}"
                try:
                    bus.publish(agent_topic, BusMessage(topic=agent_topic, payload=payload))
                except Exception as exc:
                    logger.error(
                        "gateway send error",
                        extra={
                            "event": "gateway_error",
                            "path": "send",
                            "conversation_id": message.conversation_id,
                            "agent": agent_name,
                        },
                    )
                    raise HTTPException(status_code=503, detail="bus publish failed") from exc

        # Publish a stream-visible user_message event so clients can render inbound messages
        try:
            stream_topic = f"stream:{message.conversation_id}"
            user_event = {
                "event": "user_message",
                "conversation_id": message.conversation_id,
                "sender": message.sender,
                "text": message.content,
                # RFC3339 timestamp for client-side staleness filtering
                "created_at": datetime.now(UTC).isoformat(),
            }
            bus.publish(stream_topic, BusMessage(topic=stream_topic, payload=user_event))
        except Exception as exc:
            logger.error(
                "gateway send error",
                extra={
                    "event": "gateway_error",
                    "path": "send",
                    "conversation_id": message.conversation_id,
                    "stage": "stream_user_message",
                },
            )
            raise HTTPException(status_code=503, detail="bus publish failed") from exc

        logger.info(
            "gateway send",
            extra={
                "event": "gateway_send",
                "service": "gateway",
                "conversation_id": message.conversation_id,
                "attributes": {
                    "sender": message.sender,
                    "recipient": message.recipient,
                    "content_len": len(message.content or ""),
                },
            },
        )
        metrics.increment("gateway_sends", {"conversation_id": message.conversation_id})
        return {"status": "ok", "topic": conv_topic}

    @app.get("/stream/{conversation_id}")
    async def stream(conversation_id: str, max_events: int | None = None) -> Response:
        """Server‑Sent Events stream for a conversation.

        Semantics:
        - All `token` events are forwarded as they are produced, enabling
          real‑time incremental rendering in clients.
        - `output` and `tool_step` events are forwarded as‑is.

        Parameters:
        - conversation_id: stream topic key (`stream:{conversation_id}`)
        - max_events: optional testing aid to stop after N events
        """
        topic = f"stream:{conversation_id}"
        cap = _sse_cap_bytes()

        async def event_gen() -> Any:
            last_id: str | None = None
            sent = 0
            # Simple polling loop over Bus.read
            while True:
                items = await asyncio.to_thread(
                    lambda: list(bus.read(topic, last_id=last_id, limit=100))
                )
                if items:
                    for m in items:
                        payload = m.payload
                        safe_payload = _truncate_payload_for_sse(payload, cap)
                        data = json.dumps(safe_payload, separators=(",", ":"))
                        yield f"data: {data}\n\n"
                        last_id = m.id
                        sent += 1
                        if max_events is not None and sent >= max_events:
                            return
                else:
                    # avoid tight loop when no new items are available
                    await asyncio.sleep(0.02)

        logger.info(
            "gateway stream start",
            extra={
                "event": "gateway_stream",
                "service": "gateway",
                "conversation_id": conversation_id,
            },
        )
        metrics.increment("gateway_streams", {"conversation_id": conversation_id})
        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        try:
            # Perform a harmless read on a probe topic to validate connectivity
            list(bus.read("ready:probe", last_id=None, limit=1))
            return {"status": "ok"}
        except Exception as exc:  # pragma: no cover - error path mapping
            logger.error(
                "gateway not ready",
                extra={"event": "gateway_error", "service": "gateway", "path": "ready"},
            )
            raise HTTPException(status_code=503, detail="bus not ready") from exc

    return app
