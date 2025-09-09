from __future__ import annotations

from typing import Any

from agents import function_tool

from .impl import send_signal as _send_signal_impl
from .impl import wait_for_signal as _wait_for_signal_impl


@function_tool
def signal_send(topic: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return _send_signal_impl(topic, payload or {})


@function_tool
def signal_wait(topic: str, last_id: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
    return _wait_for_signal_impl(topic, last_id=last_id, timeout_ms=timeout_ms)


__all__ = ["signal_send", "signal_wait"]
