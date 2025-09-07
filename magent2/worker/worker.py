from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import BaseStreamEvent, MessageEnvelope


class Runner(Protocol):
    """Protocol for the Agents SDK runner used by the Worker.

    Implementations must provide a streamed run interface that yields stream events.
    """

    def stream_run(
        self,
        envelope: MessageEnvelope,
    ) -> Iterable[BaseStreamEvent | dict[str, Any]]: ...


class Worker:
    """Agent Worker that reads inbound messages, runs the agent, and publishes stream events.

    - Subscribes to inbound topic: ``chat:{agent_name}``.
    - Publishes streamed events to: ``stream:{conversation_id}``.
    - Enforces at most one processed message per conversation per drain to avoid concurrency.
    """

    def __init__(self, agent_name: str, bus: Bus, runner: Runner) -> None:
        self._agent_name = agent_name
        self._bus = bus
        self._runner = runner
        self._last_inbound_id: str | None = None

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def process_available(self, limit: int = 100) -> int:
        """Process available inbound messages once and return count processed.

        Processes at most one message per conversation in a single invocation.
        """
        inbound_topic = f"chat:{self._agent_name}"
        messages = list(self._bus.read(inbound_topic, last_id=self._last_inbound_id, limit=limit))

        if not messages:
            return 0

        processed_count = 0
        processed_conversations: set[str] = set()
        last_processed_id: str | None = self._last_inbound_id

        for msg in messages:
            # Validate and normalize the envelope
            envelope = MessageEnvelope.model_validate(msg.payload)

            # Ensure we only process one message per conversation in this drain
            if envelope.conversation_id in processed_conversations:
                continue

            self._run_and_stream(envelope)
            processed_conversations.add(envelope.conversation_id)
            processed_count += 1
            last_processed_id = msg.id

        # Only advance our tail to the last processed message id, so skipped messages remain
        self._last_inbound_id = last_processed_id
        return processed_count

    def _run_and_stream(self, envelope: MessageEnvelope) -> None:
        stream_topic = f"stream:{envelope.conversation_id}"
        for event in self._runner.stream_run(envelope):
            if isinstance(event, BaseStreamEvent):
                payload: dict[str, Any] = event.model_dump()
            else:
                # The runner protocol guarantees dict[str, Any] for non-BaseStreamEvent
                payload = event
            self._bus.publish(
                stream_topic,
                BusMessage(topic=stream_topic, payload=payload),
            )
