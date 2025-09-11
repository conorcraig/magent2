from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pytest

from magent2.models.envelope import MessageEnvelope, OutputEvent


@dataclass(slots=True)
class _SimpleRunner:
    """Deterministic runner that yields a single output event per conversation."""

    outputs_by_conversation: dict[str, str] = field(default_factory=dict)

    def stream_run(self, envelope: MessageEnvelope) -> Iterable[Any]:
        text = self.outputs_by_conversation.get(envelope.conversation_id, "ok")
        yield OutputEvent(conversation_id=envelope.conversation_id, text=text)


@pytest.mark.docker
def test_worker_no_duplicate_after_restart_with_consumer_group(redis_url: str) -> None:
    """Ensure consumer group semantics prevent reprocessing after restart.

    Simulate a worker restart by constructing two workers bound to the same
    Redis consumer group. The first worker processes one inbound message and
    acknowledges it via the bus-level consumer group path. The second worker
    should see zero available messages for that group (no duplicate processing).
    """

    from magent2.bus.interface import BusMessage
    from magent2.bus.redis_adapter import RedisBus
    from magent2.worker.worker import Worker

    agent_name = f"Agent{uuid.uuid4().hex[:8]}"
    inbound_topic = f"chat:{agent_name}"
    conversation_id = f"conv-{uuid.uuid4().hex[:8]}"
    group = f"g-{uuid.uuid4()}"

    # Prepare a valid inbound envelope for the worker to process
    env = MessageEnvelope(
        conversation_id=conversation_id,
        sender="user:pytest",
        recipient=f"agent:{agent_name}",
        type="message",
        content="hello",
    )

    # First worker with consumer "c1": should process exactly one message
    bus1 = RedisBus(
        redis_url=redis_url,
        group_name=group,
        consumer_name="c1",
        block_ms=200,
    )
    runner = _SimpleRunner(outputs_by_conversation={conversation_id: "done"})
    worker1 = Worker(agent_name=agent_name, bus=bus1, runner=runner)

    # Serialize envelope using JSON mode to ensure datetime fields are serializable
    bus1.publish(
        inbound_topic,
        BusMessage(topic=inbound_topic, payload=env.model_dump(mode="json")),
    )

    processed1 = worker1.process_available(limit=10)
    assert processed1 == 1

    # Second worker with a different consumer in the same group: should find no new messages
    bus2 = RedisBus(
        redis_url=redis_url,
        group_name=group,
        consumer_name="c2",
        block_ms=200,
    )
    worker2 = Worker(agent_name=agent_name, bus=bus2, runner=runner)
    processed2 = worker2.process_available(limit=10)
    assert processed2 == 0
