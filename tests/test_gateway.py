from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

import httpx
import pytest
from httpx import ASGITransport

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import MessageEnvelope


class InMemoryBus(Bus):
    def __init__(self) -> None:
        self._topics: dict[str, list[BusMessage]] = {}

    def publish(self, topic: str, message: BusMessage) -> str:
        self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(
        self,
        topic: str,
        last_id: str | None = None,
        limit: int = 100,
    ) -> Iterable[BusMessage]:
        items = self._topics.get(topic, [])
        if last_id is None:
            return list(items[-limit:])
        start = 0
        for i, m in enumerate(items):
            if m.id == last_id:
                start = i + 1
                break
        return list(items[start : start + limit])


@pytest.mark.asyncio
async def test_gateway_send_publishes_to_chat_topic() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    env = MessageEnvelope(
        conversation_id="conv_send_1",
        sender="user:alice",
        recipient="agent:DevAgent",
        type="message",
        content="hello",
    )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/send", json=env.model_dump(mode="json"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["topic"] == "chat:conv_send_1"

    published = bus._topics.get("chat:conv_send_1", [])
    assert len(published) == 1
    assert published[0].payload["conversation_id"] == "conv_send_1"
    assert published[0].payload["content"] == "hello"


@pytest.mark.asyncio
async def test_gateway_send_validation_errors_with_422() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    # Missing required fields (e.g., recipient)
    bad_payload = {
        "id": "abc",
        "conversation_id": "c1",
        "sender": "user:alice",
        # recipient missing
        "type": "message",
        "content": "hi",
    }

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/send", json=bad_payload)
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_gateway_ready_endpoint_ok() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"


@pytest.mark.asyncio
async def test_gateway_stream_relays_sse_events() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    conversation_id = "conv_stream_1"
    stream_topic = f"stream:{conversation_id}"

    async def publisher() -> None:
        await asyncio.sleep(0.05)
        bus.publish(
            stream_topic,
            BusMessage(
                topic=stream_topic,
                payload={
                    "event": "token",
                    "conversation_id": conversation_id,
                    "text": "Hi",
                    "index": 0,
                },
            ),
        )
        await asyncio.sleep(0.05)
        bus.publish(
            stream_topic,
            BusMessage(
                topic=stream_topic,
                payload={
                    "event": "output",
                    "conversation_id": conversation_id,
                    "text": "Done",
                },
            ),
        )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        pub_task = asyncio.create_task(publisher())

        async with client.stream("GET", f"/stream/{conversation_id}?max_events=2") as resp:
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")
            seen: list[dict] = []
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    seen.append(payload)
                    if len(seen) == 2:
                        break

        await pub_task

    assert seen[0]["event"] == "token"
    assert seen[0]["text"] == "Hi"
    assert seen[1]["event"] == "output"
    assert seen[1]["text"] == "Done"
