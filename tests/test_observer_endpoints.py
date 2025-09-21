from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from tests.helpers.bus import InMemoryBus


@pytest.mark.asyncio
async def test_conversations_empty_when_index_inactive() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/conversations")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
        assert data.get("conversations") == []


@pytest.mark.asyncio
async def test_agents_empty_when_index_inactive() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/agents")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
        assert data.get("agents") == []


@pytest.mark.asyncio
async def test_graph_empty_when_index_inactive() -> None:
    from magent2.gateway.app import create_app

    bus = InMemoryBus()
    app = create_app(bus)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/graph/conv-xyz")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
        assert data.get("nodes") == []
        assert data.get("edges") == []
