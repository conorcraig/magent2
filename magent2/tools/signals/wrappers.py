from __future__ import annotations

from typing import Any

from agents import function_tool

from .impl import send_signal as _send_signal_impl
from .impl import wait_for_all as _wait_for_all_impl
from .impl import wait_for_any as _wait_for_any_impl
from .impl import wait_for_signal as _wait_for_signal_impl


@function_tool
def signal_send(topic: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _send_signal_impl(topic, payload or {})


@function_tool
def signal_wait(topic: str, last_id: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
    return _wait_for_signal_impl(topic, last_id=last_id, timeout_ms=timeout_ms)


@function_tool
def signal_wait_any(
    topics: list[str], last_ids: dict[str, str] | None = None, timeout_ms: int = 30000
) -> dict[str, Any]:
    return _wait_for_any_impl(topics, last_ids=last_ids, timeout_ms=timeout_ms)


@function_tool
def signal_wait_all(
    topics: list[str], last_ids: dict[str, str] | None = None, timeout_ms: int = 30000
) -> dict[str, Any]:
    return _wait_for_all_impl(topics, last_ids=last_ids, timeout_ms=timeout_ms)


__all__ = ["signal_send", "signal_wait", "signal_wait_any", "signal_wait_all"]
