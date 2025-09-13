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
from magent2.observability import get_json_logger, get_metrics


class SendRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender: str
    recipient: str
    type: Literal["message"] = "message"
    content: str


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()
    logger = get_json_logger("magent2.gateway")
    metrics = get_metrics()

    def _sse_event_cap_bytes() -> int | None:
        raw = (os.getenv("GATEWAY_SSE_EVENT_MAX_BYTES") or "").strip()
        if not raw:
            return None
        try:
            value = int(raw)
            return value if value > 0 else None
        except Exception:
            return None

    def _serialize_with_cap(payload: Any, cap: int | None) -> tuple[str, bool, int]:
        """Serialize payload to JSON; if over cap, return a truncated summary.

        Returns: (json_string, was_truncated, original_bytes_len)
        """
        try:
            data = json.dumps(payload, separators=(",", ":"))
        except Exception:
            # Defensive: fall back to str(payload)
            data = json.dumps({"event": "unknown", "repr": str(payload)})
        original_len = len(data.encode("utf-8"))
        if cap is None or original_len <= cap:
            return data, False, original_len

        # Build a concise, valid JSON summary that preserves the event kind
        event_kind = ""
        if isinstance(payload, dict):
            try:
                event_kind = str(payload.get("event", ""))
            except Exception:
                event_kind = ""
        summary = {
            "event": event_kind or "truncated",
            "truncated": True,
            "payload_len": original_len,
        }
        summary_json = json.dumps(summary, separators=(",", ":"))
        # Ensure summary itself respects cap; if not, drop optional fields
        if len(summary_json.encode("utf-8")) > (cap or 0):
            summary_min = {"event": event_kind or "truncated", "truncated": True}
            summary_json = json.dumps(summary_min, separators=(",", ":"))
        return summary_json, True, original_len

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
                "conversation_id": message.conversation_id,
            },
        )
        metrics.increment("gateway_sends", {"conversation_id": message.conversation_id})
        return {"status": "ok", "topic": conv_topic}

    @app.get("/stream/{conversation_id}")
    async def stream(conversation_id: str, max_events: int | None = None) -> Response:
        topic = f"stream:{conversation_id}"
        cap = _sse_event_cap_bytes()

        async def event_gen() -> Any:
            last_id: str | None = None
            sent = 0
            first_token_sent = False
            # Simple polling loop over Bus.read
            while True:
                items = await asyncio.to_thread(
                    lambda: list(bus.read(topic, last_id=last_id, limit=100))
                )
                if items:
                    for m in items:
                        payload = m.payload
                        # Filter: allow only the first token event; pass through others
                        try:
                            event_kind = str(payload.get("event", ""))
                            if event_kind == "token":
                                if first_token_sent:
                                    # skip additional token chunks for stability
                                    last_id = m.id
                                    continue
                                first_token_sent = True
                        except Exception:
                            # If payload is not dict-like, fall through without filtering
                            pass
                        json_text, was_truncated, orig_len = _serialize_with_cap(payload, cap)
                        if was_truncated:
                            # Record truncation for observability
                            logger.info(
                                "sse payload truncated",
                                extra={
                                    "event": "gateway_sse_truncated",
                                    "conversation_id": conversation_id,
                                    "payload_len": orig_len,
                                },
                            )
                            metrics.increment(
                                "gateway_sse_truncated", {"conversation_id": conversation_id}
                            )
                        yield f"data: {json_text}\n\n"
                        last_id = m.id
                        sent += 1
                        if max_events is not None and sent >= max_events:
                            return
                else:
                    # avoid tight loop when no new items are available
                    await asyncio.sleep(0.02)

        logger.info(
            "gateway stream start",
            extra={"event": "gateway_stream", "conversation_id": conversation_id},
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
                extra={"event": "gateway_error", "path": "ready"},
            )
            raise HTTPException(status_code=503, detail="bus not ready") from exc

    return app
