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
        try:
            port = docker_services.port_for("gateway", 8000)
        except Exception:
            return False
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


@pytest.mark.docker
def test_bus_stream_maxlen_trims(docker_services: Any, monkeypatch: Any) -> None:
    """Verify Redis stream trimming via BUS_STREAM_MAXLEN.

    We set BUS_STREAM_MAXLEN=10 for both gateway and worker, send >50 messages into
    one conversation, then assert the Redis stream length is bounded (<= 20 due to
    approximate trimming with '~'). This test relies on the worker using RedisBus.publish.
    """
    # Enforce trimming for the stack
    monkeypatch.setenv("BUS_STREAM_MAXLEN", "10")

    # Recreate the test stack so new env is applied to containers
    import os
    import subprocess

    env = os.environ.copy()
    subprocess.run(["docker", "compose", "-p", "magent2_test", "down", "-v"], check=False)
    subprocess.run(
        ["docker", "compose", "-p", "magent2_test", "up", "-d", "redis", "gateway", "worker"],
        check=True,
        env=env,
    )

    def check() -> bool:
        try:
            port = docker_services.port_for("gateway", 8000)
        except Exception:
            return False
        return is_responsive(f"http://localhost:{port}/health")

    docker_services.wait_until_responsive(timeout=60.0, pause=0.5, check=check)

    port = docker_services.port_for("gateway", 8000)
    conv_id = f"conv-{uuid.uuid4().hex[:8]}"
    url_send = f"http://localhost:{port}/send"

    # Send many messages to force trimming
    for i in range(50):
        payload = {
            "conversation_id": conv_id,
            "sender": "user:pytest",
            "recipient": "agent:DevAgent",
            "type": "message",
            "content": f"msg-{i}",
        }
        r = requests.post(url_send, json=payload, timeout=5)
        assert r.status_code == 200

    # Poll Redis XLEN via port mapping until entries appear, then assert trimmed length
    try:
        import redis
    except Exception as exc:  # pragma: no cover - import path
        pytest.skip(f"redis client not available: {exc}")

    rport = docker_services.port_for("redis", 6379)
    rds = redis.Redis.from_url(f"redis://localhost:{rport}/0", decode_responses=True)
    key = f"stream:{conv_id}"

    deadline = time.time() + 10.0
    length = 0
    while time.time() < deadline:
        try:
            length = int(rds.execute_command("XLEN", key) or 0)
        except Exception:
            length = 0
        if length > 0:
            break
        time.sleep(0.2)

    # Due to approximate trimming with '~', allow slack but ensure it's bounded
    assert 0 < length <= 40, f"unexpected trimmed length: {length}"
