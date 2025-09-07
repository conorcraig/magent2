from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig: pytest.Config) -> str:
    # Point pytest-docker to the project docker-compose.yml
    return os.path.join(str(pytestconfig.rootpath), "docker-compose.yml")
