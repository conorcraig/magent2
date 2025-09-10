from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
import requests


def is_responsive(url: str) -> bool:
    try:
        resp = requests.get(url, timeout=2)
        return resp.status_code < 500
    except Exception:
        return False


def collect_sse_events(resp: requests.Response, max_events: int, timeout_s: float) -> list[dict]:
    events: list[dict] = []
    deadline = time.time() + timeout_s
    for raw in resp.iter_lines():
        if time.time() > deadline:
            break
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore")
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
        if len(events) >= max_events:
            break
    return events


@pytest.mark.docker
def test_gateway_worker_docker_e2e(docker_services: Any) -> None:
    """Bring up docker-compose stack and validate end-to-end streaming."""

    # Wait for gateway health
    def check() -> bool:
        port = docker_services.port_for("gateway", 8000)
        return is_responsive(f"http://localhost:{port}/health")

    docker_services.wait_until_responsive(timeout=60.0, pause=0.5, check=check)

    # Unique conversation id to avoid stream tail collisions
    conv_id = f"conv-{uuid.uuid4().hex[:8]}"

    payload = {
        "conversation_id": conv_id,
        "sender": "user:pytest",
        "recipient": "agent:DevAgent",
        "type": "message",
        "content": "e2e-docker",
    }
    port = docker_services.port_for("gateway", 8000)
    r = requests.post(
        f"http://localhost:{port}/send",
        json=payload,
        headers={"content-type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 200, r.text

    # Stream first two events via SSE (expect user_message then token or output depending on runner)
    sse = requests.get(
        f"http://localhost:{port}/stream/{conv_id}?max_events=2",
        stream=True,
        timeout=15,
    )
    assert sse.status_code == 200

    seen = collect_sse_events(sse, max_events=2, timeout_s=15.0)

    assert len(seen) == 2, f"expected 2 events, got {seen}"
    # First event should be the synthetic user_message emitted by the Gateway
    assert seen[0]["event"] == "user_message"
    # Second event should be either a token (streaming) or output (non-streaming runner)
    assert seen[1]["event"] in {"token", "output"}
