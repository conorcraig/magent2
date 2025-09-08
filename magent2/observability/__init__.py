from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import sys
import time
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

SENSITIVE_KEYS = {"openai_api_key", "api_key", "token", "authorization", "password", "secret"}


def _iso_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _redact_value(value: Any) -> Any:
    return "[REDACTED]"


def _redact(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                redacted[k] = _redact_value(v)
            else:
                redacted[k] = _redact(v)
        return redacted
    if isinstance(obj, list | tuple):
        return [_redact(v) for v in obj]
    return obj


def _build_base_payload(record: logging.LogRecord) -> dict[str, Any]:
    return {
        "ts": _iso_now(),
        "level": record.levelname.lower(),
        "logger": record.name,
        "message": record.getMessage(),
    }


def _add_standard_extras(payload: dict[str, Any], record: logging.LogRecord) -> None:
    for attr in (
        "metadata",
        "event",
        "span_id",
        "parent_id",
        "duration_ms",
        "run_id",
        "conversation_id",
        "agent",
        "tool",
    ):
        if hasattr(record, attr):
            payload[attr] = getattr(record, attr)


def _add_span_name(payload: dict[str, Any], record: logging.LogRecord) -> None:
    span_name = getattr(record, "span_name", None)
    if span_name is not None:
        payload["name"] = span_name


def _enrich_with_context(payload: dict[str, Any]) -> None:
    ctx = get_run_context() or {}
    if isinstance(ctx, dict):
        for key in ("run_id", "conversation_id", "agent"):
            value = ctx.get(key)
            if key not in payload and value is not None:
                payload[key] = value


def _redact_metadata_in_payload(payload: dict[str, Any]) -> None:
    md = payload.get("metadata")
    if isinstance(md, dict):
        payload["metadata"] = _redact(md)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = _build_base_payload(record)
        _add_standard_extras(payload, record)
        _add_span_name(payload, record)
        _enrich_with_context(payload)
        _redact_metadata_in_payload(payload)
        return json.dumps(payload, ensure_ascii=False)


def get_json_logger(name: str = "magent2") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@dataclass
class Span:
    span_id: str
    name: str
    start_ns: int
    parent_id: str | None


class Tracer:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or get_json_logger("magent2.trace")
        self._stack: list[Span] = []

    @contextmanager
    def span(
        self,
        name: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        span_id = str(uuid.uuid4())
        parent_id = self._stack[-1].span_id if self._stack else None
        span = Span(
            span_id=span_id,
            name=name,
            start_ns=time.perf_counter_ns(),
            parent_id=parent_id,
        )

        self._logger.info(
            "span start",
            extra={
                "event": "span_start",
                "span_name": name,
                "span_id": span_id,
                "parent_id": parent_id,
                "metadata": dict(metadata or {}),
            },
        )
        self._stack.append(span)
        try:
            yield span
        finally:
            end_ns = time.perf_counter_ns()
            duration_ms = (end_ns - span.start_ns) / 1_000_000.0
            self._logger.info(
                "span end",
                extra={
                    "event": "span_end",
                    "span_name": name,
                    "span_id": span_id,
                    "parent_id": parent_id,
                    "duration_ms": duration_ms,
                },
            )
            # Pop if it is the current top
            if self._stack and self._stack[-1].span_id == span_id:
                self._stack.pop()


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}

    def increment(
        self,
        name: str,
        labels: Mapping[str, str] | None = None,
        amount: int = 1,
    ) -> None:
        label_items: tuple[tuple[str, str], ...] = tuple(sorted((labels or {}).items()))
        key = (name, label_items)
        self._counters[key] = self._counters.get(key, 0) + amount

    def snapshot(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for (name, label_items), value in sorted(self._counters.items()):
            out.append(
                {
                    "name": name,
                    "labels": dict(label_items),
                    "value": value,
                }
            )
        return out


# ----------------------------
# Run context helpers
# ----------------------------

_run_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "magent2_run_context", default=None
)


def set_run_context(run_id: str, conversation_id: str, agent: str | None = None) -> None:
    _run_context_var.set({"run_id": run_id, "conversation_id": conversation_id, "agent": agent})


def clear_run_context() -> None:
    _run_context_var.set(None)


def get_run_context() -> dict[str, Any] | None:
    return _run_context_var.get()


@contextmanager
def use_run_context(
    run_id: str, conversation_id: str, agent: str | None = None
) -> Generator[None, None, None]:
    token = _run_context_var.set(
        {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "agent": agent,
        }
    )
    try:
        yield None
    finally:
        _run_context_var.reset(token)


# ----------------------------
# Metrics singleton
# ----------------------------

_metrics_singleton: Metrics | None = None


def get_metrics() -> Metrics:
    global _metrics_singleton
    if _metrics_singleton is None:
        _metrics_singleton = Metrics()
    return _metrics_singleton


def reset_metrics() -> None:
    global _metrics_singleton
    _metrics_singleton = Metrics()
