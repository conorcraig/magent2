from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig: pytest.Config) -> str:
    return os.path.join(str(pytestconfig.rootpath), "docker-compose.yml")


@pytest.fixture(scope="session")
def redis_url(docker_services: Any) -> str:
    # Ensure Redis is up via docker_services
    def _ping() -> bool:
        try:
            import redis

            # Resolve mapped host port dynamically
            port = docker_services.port_for("redis", 6379)
            r = redis.Redis.from_url(f"redis://localhost:{port}/0")
            return bool(r.ping())
        except Exception:
            return False

    # pytest-docker waits until responsive
    docker_services.wait_until_responsive(timeout=60.0, pause=0.5, check=_ping)
    # Return resolved URL
    port = docker_services.port_for("redis", 6379)
    return f"redis://localhost:{port}/0"


@pytest.fixture()
def unique_prefix() -> str:
    # millisecond prefix to avoid collisions
    return f"test:{int(time.time() * 1000)}"
