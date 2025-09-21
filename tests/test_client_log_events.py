import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from magent2.client.cli import ClientConfig, StreamPrinter, one_shot


class DummyIterLines:
    def __init__(self, events: list[dict]):
        self._lines = []
        for ev in events:
            self._lines.append("data: " + json.dumps(ev))

    def __iter__(self):
        return iter(self._lines)


class DummyResponse:
    def __init__(self, events: list[dict]):
        self.status_code = 200
        self._iter = DummyIterLines(events)

    def iter_lines(self):
        return self._iter

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@contextmanager
def stub_stream(monkeypatch: pytest.MonkeyPatch, events: list[dict]) -> Iterator[None]:
    import httpx

    def _fake_stream(method, url, timeout=None):  # noqa: ARG001
        return DummyResponse(events)

    monkeypatch.setattr(httpx, "stream", _fake_stream)
    yield


def test_stream_printer_renders_log_and_output(monkeypatch, capsys):
    cfg = ClientConfig(
        base_url="http://localhost:8000",
        conversation_id="conv-test",
        agent_name="DevAgent",
        sender="user:test",
        log_level="info",
    )

    events = [
        {"event": "log", "level": "info", "component": "runner", "message": "hello"},
        {"event": "output", "text": "world"},
    ]

    with stub_stream(monkeypatch, events):
        sp = StreamPrinter(cfg)
        sp.start()
        # Let the background thread run one loop iteration
        sp.wait_for_final(timeout=1.0)
        sp.stop()

    out, _ = capsys.readouterr()
    assert "[log][INFO] runner: hello" in out
    assert "AI> world" in out


def test_one_shot_exit_code_zero_on_output(monkeypatch):
    # Arrange a stream that will yield a final output
    events = [
        {"event": "log", "level": "debug", "component": "runner", "message": "dbg"},
        {"event": "output", "text": "done"},
    ]

    import httpx

    def _fake_stream(method, url, timeout=None):  # noqa: ARG001
        return DummyResponse(events)

    # Prevent real network send
    def _fake_post(url, json=None, timeout=None):  # noqa: ARG001, A002
        class R:
            status_code = 200
            text = "ok"

        return R()

    monkeypatch.setattr(httpx, "stream", _fake_stream)
    monkeypatch.setattr(httpx, "post", _fake_post)

    cfg = ClientConfig(
        base_url="http://localhost:8000",
        conversation_id="conv-oneshot",
        agent_name="DevAgent",
        sender="user:test",
        log_level="info",
    )

    code = one_shot(cfg, "hello", timeout=1.0)
    assert code == 0


def test_one_shot_timeout_exit_code_nonzero(monkeypatch):
    # Stream yields no final output event
    events: list[dict] = []

    import httpx

    def _fake_stream(method, url, timeout=None):  # noqa: ARG001
        return DummyResponse(events)

    def _fake_post(url, json=None, timeout=None):  # noqa: ARG001, A002
        class R:
            status_code = 200
            text = "ok"

        return R()

    monkeypatch.setattr(httpx, "stream", _fake_stream)
    monkeypatch.setattr(httpx, "post", _fake_post)

    cfg = ClientConfig(
        base_url="http://localhost:8000",
        conversation_id="conv-oneshot",
        agent_name="DevAgent",
        sender="user:test",
        log_level="info",
    )

    code = one_shot(cfg, "hello", timeout=0.1)
    assert code == 2
