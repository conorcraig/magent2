from __future__ import annotations

from typing import Any

from agents import function_tool

from .function_tools import send_message, set_bus_for_testing


@function_tool
def chat_send(recipient: str, content: str) -> dict[str, Any]:
    """Send a chat message to a conversation or agent via Bus.

    Args:
        recipient: "chat:{conversation_id}" or "agent:{AgentName}".
        content: Non-empty message text.

    Returns:
        {"ok": bool, "envelope_id": str, "published_to": list[str]}
    """

    return send_message(recipient, content)


__all__ = ["chat_send", "send_message", "set_bus_for_testing"]
