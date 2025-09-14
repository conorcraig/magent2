from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import pytest


class _FakeResponse:
    def __init__(self, lines: Iterable[str], status_code: int = 200) -> None:
        self._lines = list(lines)
        self.status_code = status_code

    def __enter__(self) -> _FakeResponse:  # noqa: D401 - context manager enter
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401 - context manager exit
        return None

    def iter_lines(self) -> Iterable[str]:
        yield from self._lines


def _sse_line(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}"


def test_one_shot_success(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    # Import locally to access the same module objects for monkeypatching
    import scripts.client as client_mod
    from scripts.client import ClientConfig, one_shot

    created_late = "9999-01-01T00:00:00+00:00"

    # Prepare a short, deterministic SSE stream: token chunks then final output
    lines = [
        _sse_line({"event": "token", "text": "Hel", "created_at": created_late}),
        _sse_line({"event": "token", "text": "lo", "created_at": created_late}),
        _sse_line(
            {
                "event": "tool_step",
                "name": "dummy",
                "result_summary": "ok",
                "status": "success",
                "created_at": created_late,
            }
        ),
        _sse_line({"event": "output", "text": "Hello", "created_at": created_late}),
    ]

    def fake_stream(method: str, url: str, timeout: Any | None = None) -> _FakeResponse:  # noqa: ANN401
        assert method == "GET"
        assert "/stream/" in url
        return _FakeResponse(lines)

    class _PostResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "ok"

    def fake_post(url: str, json: dict[str, Any], timeout: float | None = None) -> _PostResp:  # noqa: A002
        assert url.endswith("/send")
        assert isinstance(json, dict)
        return _PostResp()

    monkeypatch.setattr(client_mod.httpx, "stream", fake_stream)
    monkeypatch.setattr(client_mod, "httpx", client_mod.httpx)
    monkeypatch.setattr(client_mod.httpx, "post", fake_post)

    cfg = ClientConfig(
        base_url="http://test",
        conversation_id="conv-test",
        agent_name="Agent",
        sender="user:test",
    )

    exit_code = one_shot(cfg, message="Hi", timeout=2.0)
    assert exit_code == 0

    out, err = capsys.readouterr()
    # Expect token rendering inline and tool + final output printed
    assert "AI> Hel" in out
    assert "[tool] dummy: ok" in out
    # We do not assert on final output duplication to avoid non-determinism across reconnects
    assert err == ""


def test_one_shot_timeout(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    import scripts.client as client_mod
    from scripts.client import ClientConfig, one_shot

    created_late = "9999-01-01T00:00:00+00:00"

    # Stream without a final output event to trigger timeout
    lines = [
        _sse_line({"event": "token", "text": "Working", "created_at": created_late}),
    ]

    def fake_stream(method: str, url: str, timeout: Any | None = None) -> _FakeResponse:  # noqa: ANN401
        return _FakeResponse(lines)

    class _PostResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "ok"

    def fake_post(url: str, json: dict[str, Any], timeout: float | None = None) -> _PostResp:  # noqa: A002
        return _PostResp()

    monkeypatch.setattr(client_mod.httpx, "stream", fake_stream)
    monkeypatch.setattr(client_mod, "httpx", client_mod.httpx)
    monkeypatch.setattr(client_mod.httpx, "post", fake_post)

    cfg = ClientConfig(
        base_url="http://test",
        conversation_id="conv-test",
        agent_name="Agent",
        sender="user:test",
    )

    exit_code = one_shot(cfg, message="Hello?", timeout=0.1)
    assert exit_code != 0
    out, err = capsys.readouterr()
    assert "timeout" in err.lower()
