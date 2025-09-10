from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterable

import uvicorn

# Ensure project root is on sys.path when running as a script
_THIS_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from magent2.bus.interface import Bus, BusMessage  # noqa: E402
from magent2.gateway.app import create_app  # noqa: E402
from magent2.runner.config import load_config  # noqa: E402
from magent2.worker.__main__ import EchoRunner  # noqa: E402
from magent2.worker.worker import Worker  # noqa: E402


class InProcessBus(Bus):
    """Thread-safe in-process Bus using per-topic append-only lists.

    - publish appends a BusMessage to a list for the topic
    - read returns messages after last_id (or tail if None), up to limit
    """

    def __init__(self) -> None:
        import threading

        self._topics: dict[str, list[BusMessage]] = {}
        self._lock = threading.RLock()

    def publish(self, topic: str, message: BusMessage) -> str:
        with self._lock:
            self._topics.setdefault(topic, []).append(message)
        return message.id

    def read(
        self, topic: str, last_id: str | None = None, limit: int = 100
    ) -> Iterable[BusMessage]:
        with self._lock:
            items = list(self._topics.get(topic, ()))
        if not items:
            return []
        if last_id is None:
            return list(items[-limit:])
        start = 0
        for i, m in enumerate(items):
            if m.id == last_id:
                start = i + 1
                break
        return list(items[start : start + max(1, limit)])


def _start_gateway(bus: Bus) -> None:
    app = create_app(bus)
    # Host/port can be overridden via env like compose
    host = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port_raw = os.getenv("GATEWAY_PORT", "8000").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 8000
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level=os.getenv("LOG_LEVEL", "info"))
    )
    server.run()


def _start_worker(bus: Bus) -> None:
    cfg = load_config()
    # Use EchoRunner unless OPENAI_API_KEY is set
    if os.getenv("OPENAI_API_KEY"):
        from magent2.worker.__main__ import build_runner_from_env

        runner = build_runner_from_env()
    else:
        runner = EchoRunner()
    worker = Worker(agent_name=cfg.agent_name, bus=bus, runner=runner)
    sleep_seconds = 0.05
    max_sleep_seconds = 0.2
    try:
        while True:
            processed = worker.process_available(limit=100)
            if processed == 0:
                time.sleep(sleep_seconds)
                sleep_seconds = min(max_sleep_seconds, sleep_seconds * 2)
            else:
                sleep_seconds = 0.05
    except KeyboardInterrupt:
        return


def main() -> None:
    # Single shared bus instance for both gateway and worker
    bus = InProcessBus()

    # Run gateway in a background thread; run worker in main thread
    t = threading.Thread(target=_start_gateway, args=(bus,), daemon=True)
    t.start()

    # Small delay to let the HTTP server bind before the worker loop starts
    time.sleep(0.1)
    _start_worker(bus)


if __name__ == "__main__":
    main()
