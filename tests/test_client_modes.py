from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from scripts.client import ClientConfig, StreamPrinter, main, one_shot


class DummyIterLines:
    def __init__(self, events: list[dict]):
        self._lines = []
        for ev in events:
            self._lines.append("data: " + json.dumps(ev))

    def __iter__(self):
        return iter(self._lines)


class DummyResponse:
    def __init__(self, events: list[dict], status_code: int = 200):
        self.status_code = status_code
        self._iter = DummyIterLines(events)

    def iter_lines(self):
        return self._iter

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@contextmanager
def stub_stream(
    monkeypatch: pytest.MonkeyPatch, events: list[dict], status_code: int = 200
) -> Iterator[None]:
    import httpx

    def _fake_stream(method, url, timeout=None):  # noqa: ARG001
        return DummyResponse(events, status_code=status_code)

    monkeypatch.setattr(httpx, "stream", _fake_stream)
    yield


def test_quiet_mode_prints_only_final(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = [
        {"event": "log", "level": "info", "component": "runner", "message": "hello"},
        {"event": "output", "text": "final"},
    ]

    with stub_stream(monkeypatch, events):
        cfg = ClientConfig(
            base_url="http://localhost:8000",
            conversation_id="conv-x",
            agent_name="DevAgent",
            sender="user:test",
            quiet=True,
        )
        sp = StreamPrinter(cfg)
        sp.start()
        sp.wait_for_final(timeout=1.0)
        sp.stop()

    out, err = capsys.readouterr()
    assert out.strip() == "final"
    assert err == ""


def test_json_mode_emits_one_json_per_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = [
        {"event": "log", "level": "info", "component": "runner", "message": "hello"},
        {"event": "output", "text": "final"},
    ]

    with stub_stream(monkeypatch, events):
        cfg = ClientConfig(
            base_url="http://localhost:8000",
            conversation_id="conv-y",
            agent_name="DevAgent",
            sender="user:test",
            json=True,
        )
        sp = StreamPrinter(cfg)
        sp.start()
        sp.wait_for_final(timeout=1.0)
        sp.stop()

    out, err = capsys.readouterr()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == len(events)
    # Each line must be valid JSON matching the corresponding event
    for i, ln in enumerate(lines):
        assert json.loads(ln) == events[i]
    assert err == ""


def test_max_events_limits_processed_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = [
        {"event": "output", "text": "one"},
        {"event": "output", "text": "two"},
        {"event": "output", "text": "three"},
    ]

    with stub_stream(monkeypatch, events):
        cfg = ClientConfig(
            base_url="http://localhost:8000",
            conversation_id="conv-z",
            agent_name="DevAgent",
            sender="user:test",
            max_events=2,
        )
        sp = StreamPrinter(cfg)
        sp.start()
        # Give the background thread time to consume events
        sp.wait_for_final(timeout=0.2)
        sp.stop()

    out, _ = capsys.readouterr()
    # Pretty mode prefixes final outputs with "AI> "; only first two should appear
    assert "AI> one" in out
    assert "AI> two" in out
    assert "AI> three" not in out


def test_connect_failed_exit_code_4(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise_stream(method, url, timeout=None):  # noqa: ARG001
        raise RuntimeError("connect failed")

    def _fake_post(url, json=None, timeout=None):  # noqa: ARG001, A002
        class R:
            status_code = 200
            text = "ok"

        return R()

    monkeypatch.setattr(httpx, "stream", _raise_stream)
    monkeypatch.setattr(httpx, "post", _fake_post)

    cfg = ClientConfig(
        base_url="http://localhost:8000",
        conversation_id="conv-err",
        agent_name="DevAgent",
        sender="user:test",
    )
    code = one_shot(cfg, "hello", timeout=0.2)
    assert code == 4


def test_send_failed_exit_code_3(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _ok_stream(method, url, timeout=None):  # noqa: ARG001
        # Minimal stream that just opens and yields nothing
        return DummyResponse([])

    def _bad_post(url, json=None, timeout=None):  # noqa: ARG001, A002
        class R:
            status_code = 500
            text = "err"

        return R()

    monkeypatch.setattr(httpx, "stream", _ok_stream)
    monkeypatch.setattr(httpx, "post", _bad_post)

    cfg = ClientConfig(
        base_url="http://localhost:8000",
        conversation_id="conv-err",
        agent_name="DevAgent",
        sender="user:test",
    )
    code = one_shot(cfg, "hello", timeout=0.5)
    assert code == 3


def test_main_args_mutually_exclusive_modes_exit_5(monkeypatch: pytest.MonkeyPatch) -> None:
    # Minimal viable args to reach parse_args validation
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--base-url",
                "http://localhost:8000",
                "--json",
                "--quiet",
                "--message",
                "hi",
                "--timeout",
                "0.1",
            ]
        )
    assert int(excinfo.value.code) == 5
