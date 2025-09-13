from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openai import OpenAI

from magent2.models.envelope import BaseStreamEvent, MessageEnvelope, OutputEvent


class OpenAIResponsesRunner:
    """Runner that calls OpenAI Responses API for conversational replies.

    This runner does a simple non-streamed call per turn and emits a single
    OutputEvent. It does not perform function/tool calls; those are handled
    by higher-level orchestration if present.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._client = OpenAI()

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        # Minimal request to the Responses API
        # We pass only the latest user content to keep behavior simple.
        text = envelope.content or ""
        try:
            resp = self._client.responses.create(model=self._model, input=text)
            # Prefer the baked output_text shortcut if present
            output_text = getattr(resp, "output_text", None)
            final_text = str(output_text) if output_text is not None else str(resp)
        except Exception as exc:  # noqa: BLE001
            final_text = f"[error] OpenAI Responses call failed: {exc}"
        yield OutputEvent(conversation_id=envelope.conversation_id, text=final_text)


__all__ = ["OpenAIResponsesRunner"]
