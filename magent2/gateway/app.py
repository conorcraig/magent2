from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from magent2.bus.interface import Bus, BusMessage


class SendRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender: str
    recipient: str
    type: Literal["message"] = "message"
    content: str


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()

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
                    raise HTTPException(status_code=503, detail="bus publish failed") from exc

        # Do not emit a synthetic user_message event here to keep stream ordering predictable

        return {"status": "ok", "topic": conv_topic}

    @app.get("/stream/{conversation_id}")
    async def stream(conversation_id: str, max_events: int | None = None) -> Response:
        topic = f"stream:{conversation_id}"

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
                        data = json.dumps(payload)
                        yield f"data: {data}\n\n"
                        last_id = m.id
                        sent += 1
                        if max_events is not None and sent >= max_events:
                            return
                else:
                    # avoid tight loop when no new items are available
                    await asyncio.sleep(0.02)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        try:
            # Perform a harmless read on a probe topic to validate connectivity
            list(bus.read("ready:probe", last_id=None, limit=1))
            return {"status": "ok"}
        except Exception as exc:  # pragma: no cover - error path mapping
            raise HTTPException(status_code=503, detail="bus not ready") from exc

    return app
