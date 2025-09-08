from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _docker_available() -> bool:
    """Detect whether Docker engine is available for tests.

    Logic:
    - Respect explicit override: FORCE_DOCKER_TESTS=1 forces availability.
    - Require docker CLI to be present in PATH.
    - Try `docker info` with a generous timeout (engine health).
    - Fall back to checking the Compose plugin as a weak signal.
    """
    if os.environ.get("FORCE_DOCKER_TESTS") == "1":
        return True

    docker = shutil.which("docker")
    if not docker:
        return False

    try:
        proc = subprocess.run(
            [docker, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass

    # Weak fallback: compose plugin present (does not guarantee engine availability).
    # Useful in some CI setups where `docker info` is slow/flaky.
    try:
        proc2 = subprocess.run(
            [docker, "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return proc2.returncode == 0
    except Exception:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests that require Docker if Docker isn't available.

    We treat tests as Docker-dependent if they:
    - are marked with the 'docker' marker, or
    - use fixtures that depend on Docker (e.g., 'docker_services' or 'redis_url').
    """
    if _docker_available():
        return

    skip_marker = pytest.mark.skip(reason="Docker not available; skipping docker-dependent tests")

    for item in items:
        # Direct markers
        if "docker" in item.keywords:
            item.add_marker(skip_marker)
            continue

        # Direct fixture usage
        fixt_names = set(getattr(item, "fixturenames", []) or [])

        # Include transitive fixture dependencies if available
        closure = set(getattr(getattr(item, "_fixtureinfo", None), "names_closure", []) or [])
        all_fixtures = fixt_names | closure

        if {"docker_services", "redis_url"} & all_fixtures:
            item.add_marker(skip_marker)
