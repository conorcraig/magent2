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
    return f"data: {json.dumps(payload, separators=(',', ':'))}"


@pytest.mark.parametrize("mode", ["pretty", "json", "quiet"])  # exercise all render modes lightly
def test_client_dedupes_replayed_tokens_across_reconnects(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, mode: str
) -> None:  # noqa: ANN401
    import magent2.client.cli as client_mod
    from magent2.client.cli import ClientConfig, one_shot

    calls: list[dict[str, Any]] = []

    def fake_stream(
        method: str, url: str, timeout: Any | None = None, headers: dict[str, str] | None = None
    ):  # noqa: ANN401
        calls.append({"url": url, "headers": headers or {}})
        if len(calls) == 1:
            # First connection: one token, then disconnect
            return _FakeResponse(
                [
                    "id: 1",
                    _sse_line({"event": "token", "text": "A", "index": 0}),
                ]
            )
        # Second connection: server replays same token (index 0), then final output
        return _FakeResponse(
            [
                "id: 1",
                _sse_line({"event": "token", "text": "A", "index": 0}),
                "id: 2",
                _sse_line({"event": "output", "text": "Z"}),
            ]
        )

    class _PostResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = "ok"

    def fake_post(url: str, json: dict[str, Any], timeout: float | None = None) -> _PostResp:  # noqa: A002
        return _PostResp()

    monkeypatch.setattr(client_mod, "httpx", client_mod.httpx)
    monkeypatch.setattr(client_mod.httpx, "stream", fake_stream)
    monkeypatch.setattr(client_mod.httpx, "post", fake_post)

    cfg = ClientConfig(
        base_url="http://test",
        conversation_id="conv-reconnect",
        agent_name="Agent",
        sender="user:test",
    )
    if mode == "json":
        cfg.json = True
    if mode == "quiet":
        cfg.quiet = True

    # Run one-shot with a short timeout; final output will be received on reconnect
    exit_code = one_shot(cfg, message="Hi", timeout=2.0)
    assert exit_code == 0

    out, err = capsys.readouterr()
    # Token "A" should appear at most once in pretty mode; in json mode, ensure a single token
    if mode == "json":
        tokens = [ln for ln in out.splitlines() if '"event":"token"' in ln]
        assert len(tokens) == 1
    elif mode == "quiet":
        # Quiet mode only prints the final output line
        assert out.strip() == "Z"
    else:
        assert out.count("A") == 1

    # Verify Last-Event-ID used on reconnect
    assert any("Last-Event-ID" in c.get("headers", {}) for c in calls[1:])
