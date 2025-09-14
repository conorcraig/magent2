from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

SENSITIVE_KEYS = {"openai_api_key", "api_key", "token", "authorization", "password", "secret"}


def _iso_now() -> str:
    # Original precise ISO8601 with timezone offset
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
    service = (
        getattr(record, "service", None)
        or getattr(record, "svc", None)
        or os.getenv("SERVICE_NAME")
    )
    return {
        "ts": _iso_now(),
        "level": record.levelname.lower(),
        "service": service,
        "msg": record.getMessage(),
    }


def _add_standard_extras(payload: dict[str, Any], record: logging.LogRecord) -> None:
    # Include standardized fields; caller should pass 'attributes' explicitly when needed
    for attr in (
        "event",
        "span_id",
        "parent_id",
        "duration_ms",
        "run_id",
        "conversation_id",
        "agent",
        "tool",
        "trace_id",
        "request_id",
        "attributes",
        "metadata",
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


def _redact_attributes_in_payload(payload: dict[str, Any]) -> None:
    attributes = payload.get("attributes")
    if isinstance(attributes, dict):
        payload["attributes"] = _redact(attributes)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = _build_base_payload(record)
        _add_standard_extras(payload, record)
        _add_span_name(payload, record)
        _enrich_with_context(payload)
        _redact_attributes_in_payload(payload)
        # Attach error fields if present, keeping the JSON single-line
        if record.exc_info:
            try:
                exc_type, exc_value, exc_tb = record.exc_info
                payload["err_type"] = getattr(exc_type, "__name__", str(exc_type))
                if exc_value is not None:
                    payload["err"] = str(exc_value)
                try:
                    payload["stack"] = self.formatException(record.exc_info)
                except Exception:
                    pass
            except Exception:
                pass
        return json.dumps(payload, ensure_ascii=False)


class ConsoleLogFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(message)s")

    @staticmethod
    def _shorten(value: str | None, *, n: int = 8) -> str:
        if not value:
            return "-"
        return value[:n]

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # Base fields
        ts = _iso_now()[11:19]  # HH:MM:SS
        level = record.levelname.upper()
        name = record.name
        msg = record.getMessage()

        # Extras (if present)
        run_id = getattr(record, "run_id", None)
        conv_id = getattr(record, "conversation_id", None)
        event = getattr(record, "event", None)
        agent = getattr(record, "agent", None)
        svc = getattr(record, "service", None) or getattr(record, "svc", None)

        parts: list[str] = [ts, level]
        if svc:
            parts.append(str(svc))
        else:
            # fall back to logger name if svc missing
            parts.append(name)
        if event:
            parts.append(str(event))
        if agent:
            parts.append(f"agent={agent}")
        if conv_id:
            parts.append(f"conv={self._shorten(str(conv_id))}")
        if run_id:
            parts.append(f"run={self._shorten(str(run_id))}")
        # Show key kv summary when available on run completion for readability
        kv = getattr(record, "kv", None)
        parts.append("-")
        if event == "run_completed" and isinstance(kv, dict):
            tc = kv.get("tool_calls")
            te = kv.get("tool_errors")
            parts.append(f"kv.tool_calls={tc} kv.tool_errors={te}")
        parts.append(msg)
        return " ".join(parts)


def _choose_formatter() -> logging.Formatter:
    format_pref = (os.getenv("LOG_FORMAT") or "").strip().lower() or "auto"
    if format_pref == "auto":
        try:
            if sys.stdout.isatty():
                return ConsoleLogFormatter()
        except Exception:
            pass
        return JsonLogFormatter()
    if format_pref == "console":
        return ConsoleLogFormatter()
    return JsonLogFormatter()


def _parse_level(value: str | None, default: int = logging.INFO) -> int:
    name = (value or "").strip().upper()
    if not name:
        return default
    level = getattr(logging, name, None)
    return level if isinstance(level, int) else default


def _level_for_logger(logger_name: str) -> int:
    base_level = _parse_level(os.getenv("LOG_LEVEL"), logging.INFO)
    overrides = (os.getenv("LOG_MODULE_LEVELS") or "").strip()
    if not overrides:
        return base_level
    for entry in overrides.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        prefix, lvl = entry.split("=", 1)
        prefix = prefix.strip()
        if not prefix:
            continue
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return _parse_level(lvl, base_level)
    return base_level


def get_json_logger(name: str = "magent2") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_choose_formatter())
        logger.addHandler(handler)
        logger.setLevel(_level_for_logger(name))
        logger.propagate = False
    return logger


def configure_uvicorn_logging() -> None:
    """Bind uvicorn loggers to use our formatter/levels.

    - Applies to: "uvicorn", "uvicorn.error", "uvicorn.access".
    - Replaces existing handlers with a single StreamHandler using our formatter.
    - Respects LOG_LEVEL/LOG_MODULE_LEVELS via _level_for_logger.
    """
    try:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            # Remove existing handlers to avoid double logging
            for h in list(lg.handlers):
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass
                try:
                    h.close()
                except Exception:
                    pass
            handler = logging.StreamHandler(stream=sys.stdout)
            handler.setFormatter(_choose_formatter())
            lg.addHandler(handler)
            lg.setLevel(_level_for_logger(name))
            lg.propagate = False
    except Exception:
        # Best-effort; avoid breaking app startup if logging tweak fails
        pass


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
                "kv": dict(metadata or {}),
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
