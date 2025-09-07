from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from magent2.bus.interface import Bus, BusMessage


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def access_log_filter(request: Request, call_next: Any) -> Response:
        # Skip logging for health endpoint; log basic info for others
        start = time.perf_counter()
        response: Response = await call_next(request)
        if request.url.path != "/health":
            dur_ms = int((time.perf_counter() - start) * 1000)
            # Minimal, readable line; keep simple to avoid noise
            print(f"REQ {request.method} {request.url.path} -> {response.status_code} {dur_ms}ms")
        return response

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
