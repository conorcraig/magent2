from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from magent2.bus.interface import Bus, BusMessage
from magent2.bus.utils import compute_publish_topics
from magent2.observability import configure_uvicorn_logging, get_json_logger, get_metrics


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
        payload = message.model_dump(mode="json")
        conv_topic = f"chat:{message.conversation_id}"

        def _publish_or_503(topic: str, *, stage: str | None = None, extra: dict[str, Any] | None = None) -> None:
            try:
                bus.publish(topic, BusMessage(topic=topic, payload=payload))
            except Exception as exc:  # pragma: no cover - error path mapping
                fields: dict[str, Any] = {
                    "event": "gateway_error",
                    "path": "send",
                    "conversation_id": message.conversation_id,
                }
                if stage:
                    fields["stage"] = stage
                if isinstance(extra, dict):
                    fields.update(extra)
                logger.error("gateway send error", extra=fields)
                raise HTTPException(status_code=503, detail="bus publish failed") from exc

        # Publish to conversation and optional agent topics
        for topic in compute_publish_topics(message.recipient, message.conversation_id):
            extra: dict[str, Any] | None = None
            if topic != conv_topic and topic.startswith("chat:"):
                extra = {"agent": topic.split(":", 1)[1]}
            _publish_or_503(topic, extra=extra)

        # Publish a stream-visible user_message event so clients can render inbound messages
        stream_topic = f"stream:{message.conversation_id}"
        user_event = {
            "event": "user_message",
            "conversation_id": message.conversation_id,
            "sender": message.sender,
            "text": message.content,
            # RFC3339 timestamp for client-side staleness filtering
            "created_at": datetime.now(UTC).isoformat(),
        }
        stream_payload = BusMessage(topic=stream_topic, payload=user_event).payload
        try:
            bus.publish(stream_topic, BusMessage(topic=stream_topic, payload=stream_payload))
        except Exception as exc:  # pragma: no cover - error path mapping
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
                        data = json.dumps(m.payload)
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
