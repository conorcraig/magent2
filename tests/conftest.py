from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from _pytest.terminal import TerminalReporter

# ROOT is defined for reference but we don't need to manipulate sys.path
# since we're using proper Python packaging
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig: pytest.Config) -> str:
    return os.path.join(str(pytestconfig.rootpath), "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    """Use a dedicated project name so test stack never collides with dev stack."""
    return "magent2_test"


@pytest.fixture(scope="session", autouse=True)
def _compose_fixed_test_ports() -> None:
    """Default test stack to fixed, non-conflicting host ports.

    - Dev defaults (compose): gateway 8000, redis 6379
    - Test defaults (pytest): gateway 18000, redis 16379
    Override via env if needed.
    """
    os.environ.setdefault("GATEWAY_PORT", "18000")
    os.environ.setdefault("REDIS_PORT", "16379")


@pytest.fixture(scope="session", autouse=True)
def _disable_openai_for_docker_tests() -> None:
    """Ensure docker-based tests use EchoRunner, not real OpenAI APIs."""
    os.environ["OPENAI_API_KEY"] = ""


def _wait_until(timeout_s: float, pause_s: float, check: Callable[[], bool]) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if check():
            return True
        time.sleep(pause_s)
    return False


def _redis_ping(url: str) -> bool:
    try:
        import redis

        r = redis.Redis.from_url(url)
        return bool(r.ping())
    except Exception:
        return False


def _local_redis_available() -> bool:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return _redis_ping(url)


@pytest.fixture(scope="session")
def redis_url(pytestconfig: pytest.Config, docker_services: Any) -> str:
    """Provide a Redis URL, preferring local/ENV when available, otherwise Docker.

    Priority:
    1) REDIS_URL env if reachable
    2) localhost:6379 if reachable
    3) pytest-docker services (if Docker available)
    """

    # 1) Respect explicit REDIS_URL if reachable
    env_url = os.getenv("REDIS_URL")
    if env_url:
        ok = _wait_until(10.0, 0.2, lambda: _redis_ping(env_url))
        if ok:
            return env_url

    # 2) Try default local Redis
    local_url = "redis://localhost:6379/0"
    if _wait_until(3.0, 0.2, lambda: _redis_ping(local_url)):
        return local_url

    # 3) Fall back to Docker if available
    if _docker_available():

        def _ping() -> bool:
            try:
                import redis

                port = docker_services.port_for("redis", 6379)
                return bool(redis.Redis.from_url(f"redis://localhost:{port}/0").ping())
            except Exception:
                return False

        docker_services.wait_until_responsive(timeout=60.0, pause=0.5, check=_ping)
        port = docker_services.port_for("redis", 6379)
        return f"redis://localhost:{port}/0"

    pytest.skip(
        "Redis not available locally and Docker not available; skipping Redis-dependent tests"
    )


@pytest.fixture()
def unique_prefix() -> str:
    # millisecond prefix to avoid collisions
    return f"test:{int(time.time() * 1000)}"


@lru_cache(maxsize=1)
def _docker_available() -> bool:
    """Basic Docker health check.

    Standard practice: ensure `docker` CLI exists and `docker ps` succeeds.
    """
    if os.environ.get("FORCE_DOCKER_TESTS") == "1":
        return True

    docker = shutil.which("docker")
    if not docker:
        return False

    try:
        proc = subprocess.run(
            [docker, "ps", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.returncode == 0
    except Exception:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests that require unavailable external deps.

    - Always skip tests marked 'docker' if Docker is unavailable.
    - Skip tests that explicitly require 'docker_services' if Docker is unavailable.
    - Do NOT skip tests using 'redis_url' if a local Redis is reachable or REDIS_URL is set.
    """
    docker_ok = _docker_available()
    redis_ok = _local_redis_available()

    for item in items:
        # Direct markers
        if "docker" in item.keywords and not docker_ok:
            item.add_marker(
                pytest.mark.skip(reason="Docker not available; skipping docker-marked test")
            )
            continue

        # Direct fixture usage
        fixt_names = set(getattr(item, "fixturenames", []) or [])
        closure = set(getattr(getattr(item, "_fixtureinfo", None), "names_closure", []) or [])
        all_fixtures = fixt_names | closure

        # If a test explicitly uses docker_services, require Docker
        if "docker_services" in all_fixtures and not docker_ok:
            item.add_marker(
                pytest.mark.skip(reason="Docker not available; skipping docker_services test")
            )
            continue

        # If a test uses redis_url, allow it when local Redis/ENV is available
        if "redis_url" in all_fixtures and not (redis_ok or docker_ok or os.getenv("REDIS_URL")):
            item.add_marker(
                pytest.mark.skip(reason="Redis not available; set REDIS_URL or start local Redis")
            )


def pytest_terminal_summary(terminalreporter: TerminalReporter, exitstatus: int) -> None:  # noqa: D401
    """Print a concise pointer to the JSON report for detailed failures.

    Keeps terminal output minimal and directs agents/tools to the structured file.
    """
    # Only print pointer when tests were collected
    total = getattr(terminalreporter, "_numcollected", 0)
    if total:
        terminalreporter.write_sep(
            "-",
            "Results: reports/pytest-report.json (JSON). See this file for full details.",
        )


def _compact_failure_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    outcome = entry.get("outcome") or ""
    if not (isinstance(outcome, str) and outcome in {"failed", "error"}):
        return None
    compact: dict[str, Any] = {"outcome": outcome}
    nodeid = entry.get("nodeid")
    lineno = entry.get("lineno")
    if isinstance(nodeid, str) and nodeid:
        compact["nodeid"] = nodeid
    if isinstance(lineno, int):
        compact["lineno"] = lineno
    call = entry.get("call") if isinstance(entry.get("call"), dict) else None
    if isinstance(call, dict):
        crash = call.get("crash") if isinstance(call.get("crash"), dict) else None
        if isinstance(crash, dict):
            crash_out: dict[str, Any] = {}
            cpath = crash.get("path")
            clineno = crash.get("lineno")
            cmsg = crash.get("message")
            if isinstance(cpath, str) and cpath:
                crash_out["path"] = cpath
            if isinstance(clineno, int):
                crash_out["lineno"] = clineno
            if isinstance(cmsg, str) and cmsg:
                crash_out["message"] = cmsg
            if crash_out:
                compact["crash"] = crash_out
        lrepr = call.get("longrepr")
        if isinstance(lrepr, str) and lrepr:
            compact["longrepr"] = lrepr
    return compact


def _filter_failures(tests: list[Any]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in tests:
        if not isinstance(entry, dict):
            continue
        compact = _compact_failure_entry(entry)
        if compact is not None:
            filtered.append(compact)
    return filtered


def _drop_collectors(json_report: Any) -> None:
    if isinstance(json_report, dict) and "collectors" in json_report:
        try:
            del json_report["collectors"]
        except Exception:
            pass


def _mark_only_failures(json_report: Any) -> None:
    existing_meta = json_report.get("meta") if isinstance(json_report.get("meta"), dict) else None
    meta: dict[str, Any] = existing_meta if isinstance(existing_meta, dict) else {}
    meta["only_failures"] = True
    json_report["meta"] = meta


def pytest_json_modifyreport(json_report: Any) -> None:  # noqa: D401
    """Minimize JSON report size for agents by keeping only failures/errors."""
    try:
        tests = json_report.get("tests")
        if isinstance(tests, list):
            json_report["tests"] = _filter_failures(tests)
        _drop_collectors(json_report)
        _mark_only_failures(json_report)
    except Exception:
        # Never fail the test run due to reporting tweaks
        pass
