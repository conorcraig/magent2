from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageEnvelope(BaseModel):
    """Transport-agnostic message envelope shared across components.

    Defines canonical fields for any message sent to an agent. Delivery transport
    (Redis, HTTP, etc.) is intentionally not encoded here.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender: str
    recipient: str
    type: Literal["message", "control"]
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class BaseStreamEvent(BaseModel):
    """Base fields for streamed events emitted during an agent run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class TokenEvent(BaseStreamEvent):
    event: Literal["token"] = "token"
    text: str
    index: int


class ToolStepEvent(BaseStreamEvent):
    event: Literal["tool_step"] = "tool_step"
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_summary: str | None = None


class OutputEvent(BaseStreamEvent):
    event: Literal["output"] = "output"
    text: str
    usage: dict[str, Any] | None = None


__all__ = [
    "MessageEnvelope",
    "BaseStreamEvent",
    "TokenEvent",
    "ToolStepEvent",
    "OutputEvent",
]
