# e2e conftest for shared fixtures (reserved)

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig: pytest.Config) -> str:
    # Point pytest-docker to the project docker-compose.yml
    return os.path.join(str(pytestconfig.rootpath), "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    # Isolate test stack from any developer stack
    return "magent2_test"


@pytest.fixture(scope="session", autouse=True)
def _compose_ephemeral_ports() -> None:
    """Default test stack to fixed, non-conflicting host ports.

    Use ports distinct from dev defaults (8000/6379) to allow parallel stacks.
    Override by setting env vars before running pytest.
    """
    os.environ.setdefault("GATEWAY_PORT", "18000")
    os.environ.setdefault("REDIS_PORT", "16379")
