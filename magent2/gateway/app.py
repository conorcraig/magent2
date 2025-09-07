from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from magent2.bus.interface import Bus, BusMessage


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:  # lightweight healthcheck endpoint
        return {"status": "ok"}

    @app.post("/send")
    async def send(message: dict[str, Any]) -> dict[str, Any]:
        # Validate minimal shape via required fields
        try:
            conversation_id = str(message["conversation_id"])  # noqa: F841
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail="invalid envelope") from exc

        # Always publish to conversation topic for compatibility
        conv_topic = f"chat:{message['conversation_id']}"
        bus.publish(conv_topic, BusMessage(topic=conv_topic, payload=message))

        # Additionally publish to agent topic when recipient hints an agent
        recipient = str(message.get("recipient", ""))
        if recipient.startswith("agent:"):
            agent_name = recipient.split(":", 1)[1] or ""
            if agent_name:
                agent_topic = f"chat:{agent_name}"
                bus.publish(agent_topic, BusMessage(topic=agent_topic, payload=message))

        # Emit a user_message event to the stream so all subscribers see the inbound message
        try:
            stream_topic = f"stream:{message['conversation_id']}"
            created_at = datetime.datetime.now(datetime.UTC).isoformat()
            user_evt = {
                "event": "user_message",
                "conversation_id": message["conversation_id"],
                "sender": message.get("sender", "user"),
                "text": message.get("content", ""),
                "created_at": created_at,
            }
            bus.publish(stream_topic, BusMessage(topic=stream_topic, payload=user_evt))
        except Exception:
            # Do not fail the request if stream fan-out fails; inbound chat publish already happened
            pass

        return {"status": "ok", "topic": conv_topic}

    @app.get("/stream/{conversation_id}")
    async def stream(conversation_id: str, max_events: int | None = None) -> Response:
        topic = f"stream:{conversation_id}"

        async def event_gen() -> Any:
            last_id: str | None = None
            sent = 0
            # Simple polling loop over Bus.read
            while True:
                items = list(bus.read(topic, last_id=last_id, limit=100))
                if items:
                    for m in items:
                        data = json.dumps(m.payload)
                        yield f"data: {data}\n\n"
                        last_id = m.id
                        sent += 1
                        if max_events is not None and sent >= max_events:
                            return
                # avoid tight loop
                await asyncio.sleep(0.02)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    return app
