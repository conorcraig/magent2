from __future__ import annotations

import json
from typing import Any

from magent2.observability import Metrics, Tracer, get_json_logger


def _parse_json_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.strip().splitlines() if line.strip()]


def test_json_logger_redacts_and_formats(capsys: Any) -> None:
    logger = get_json_logger("obs-test")
    logger.setLevel(20)  # INFO
    logger.info(
        "hello",
        extra={
            "attributes": {
                "OPENAI_API_KEY": "sk-abc",
                "token": "XYZ",
                "safe": "ok",
            }
        },
    )

    out = capsys.readouterr().out
    lines = _parse_json_lines(out)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["msg"] == "hello"
    assert rec["level"] == "info"
    attributes = rec.get("attributes")
    assert isinstance(attributes, dict)
    assert attributes["safe"] == "ok"
    assert attributes["OPENAI_API_KEY"] == "[REDACTED]"
    assert attributes["token"] == "[REDACTED]"


def test_tracer_spans_and_parent_child(capsys: Any) -> None:
    logger = get_json_logger("obs-test-trace")
    tracer = Tracer(logger)

    with tracer.span("parent", {"conversation_id": "c1"}) as _parent_span:
        with tracer.span("child", {"tool": "terminal.run"}):
            pass

    out = capsys.readouterr().out
    lines = _parse_json_lines(out)
    starts = [d for d in lines if d.get("event") == "span_start"]
    ends = [d for d in lines if d.get("event") == "span_end"]

    assert len(starts) == 2
    assert len(ends) == 2

    parent_start = next(d for d in starts if d.get("name") == "parent")
    child_start = next(d for d in starts if d.get("name") == "child")

    assert "span_id" in parent_start and parent_start["span_id"]
    assert "span_id" in child_start and child_start["span_id"]
    assert child_start.get("parent_id") == parent_start.get("span_id")

    parent_end = next(d for d in ends if d.get("span_id") == parent_start["span_id"])
    assert parent_end["name"] == "parent"
    assert isinstance(parent_end.get("duration_ms"), int | float)


def test_metrics_counters_increment_and_snapshot() -> None:
    metrics = Metrics()
    metrics.increment("tool_calls", {"tool": "terminal"}, 2)
    metrics.increment("tool_calls", {"tool": "terminal"})

    snap = metrics.snapshot()
    entry = next(
        e for e in snap if e["name"] == "tool_calls" and e["labels"].get("tool") == "terminal"
    )
    assert entry["value"] == 3
