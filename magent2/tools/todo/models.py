from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from pydantic import BaseModel, Field


class Task(BaseModel):
    """A simple Todo task persisted in the store.

    - Sorted by created_at for listing within a conversation
    - Metadata is an unstructured dict for extensibility
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    title: str
    completed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: _dt.datetime = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC))


__all__ = ["Task"]
