from __future__ import annotations

import pytest


@pytest.mark.docker
def test_observer_index_writes_and_reads(redis_url: str) -> None:
    from magent2.bus.redis_adapter import RedisBus
    from magent2.observability.index import ObserverIndex

    bus = RedisBus(redis_url=redis_url)
    idx = ObserverIndex.from_bus(bus)

    # Ensure enabled
    assert idx.is_active()

    cid = "conv-t1"
    idx.record_user_message(cid, "user:alice", "agent:DevAgent", "hi", None)
    idx.record_run_started("DevAgent", cid, None)
    idx.record_run_completed("DevAgent", cid, None, errored=False)

    convs = idx.list_conversations(limit=10)
    assert any(c.get("id") == cid for c in convs)

    agents = idx.list_agents()
    assert any(a.get("name") == "DevAgent" for a in agents)

    g = idx.get_graph(cid)
    assert isinstance(g, dict)
    assert any(n.get("id") == "user:alice" for n in g.get("nodes", []))
    assert any(e.get("from") == "user:alice" for e in g.get("edges", []))
