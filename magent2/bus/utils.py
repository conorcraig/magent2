from __future__ import annotations

from typing import Iterable


def compute_publish_topics(recipient: str, conversation_id: str) -> list[str]:
    """Return list of topics to publish for a chat message.

    Always includes conversation topic `chat:{conversation_id}`.
    If recipient is of the form `agent:{name}` with a non-empty name, also
    include the agent topic `chat:{name}`.
    """
    topics: list[str] = [f"chat:{conversation_id}"]
    rec = (recipient or "").strip()
    if rec.startswith("agent:"):
        _, _, name = rec.partition(":")
        if name:
            topics.append(f"chat:{name}")
    return topics


__all__: Iterable[str] = ["compute_publish_topics"]

