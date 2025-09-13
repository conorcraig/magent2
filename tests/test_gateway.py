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


@pytest.mark.asyncio
async def test_gateway_stream_applies_payload_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from magent2.gateway.app import create_app

    # Cap very small to force truncation
    monkeypatch.setenv("GATEWAY_SSE_EVENT_MAX_BYTES", "32")

    bus = InMemoryBus()
    app = create_app(bus)

    conversation_id = "conv_stream_cap"
    stream_topic = f"stream:{conversation_id}"

    # Publish a large payload event
    big_text = "x" * 500
    bus.publish(
        stream_topic,
        BusMessage(
            topic=stream_topic,
            payload={
                "event": "output",
                "conversation_id": conversation_id,
                "text": big_text,
            },
        ),
    )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with client.stream("GET", f"/stream/{conversation_id}?max_events=1") as resp:
            assert resp.status_code == 200
            payload = None
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    break
    assert payload is not None
    # Expect a truncated summary with event preserved (or 'truncated') and a flag
    assert payload.get("truncated") is True
    assert payload.get("event") in {"output", "truncated"}


@pytest.mark.asyncio
async def test_gateway_send_emits_user_message_event_to_stream() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    conversation_id = "conv_stream_user"
    stream_topic = f"stream:{conversation_id}"

    env = MessageEnvelope(
        conversation_id=conversation_id,
        sender="user:alice",
        recipient="agent:DevAgent",
        type="message",
        content="hi there",
    )

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/send", json=env.model_dump(mode="json"))
        assert resp.status_code == 200

    # Validate that a user_message event was published to the stream topic
    published = bus._topics.get(stream_topic, [])
    assert len(published) == 1
    payload = published[0].payload
    assert payload.get("event") == "user_message"
    assert payload.get("conversation_id") == conversation_id
    assert payload.get("sender") == "user:alice"
    assert payload.get("text") == "hi there"
    # created_at should be an ISO8601/RFC3339 string
    assert isinstance(payload.get("created_at"), str)
