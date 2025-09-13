from __future__ import annotations

import random
import time
import traceback
import uuid
from collections.abc import Iterable
from typing import Any, Protocol

from magent2.bus.interface import Bus, BusMessage
from magent2.models.envelope import BaseStreamEvent, MessageEnvelope
from magent2.observability import get_json_logger, get_metrics, use_run_context


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
        # In-memory fallbacks for idempotency and single-flight when Redis is not available
        self._processed_by_conversation: dict[str, set[str]] = {}
        self._locks_in_memory: set[str] = set()

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

            # Idempotency: skip if this message id was already processed
            if self._already_processed(envelope.conversation_id, envelope.id):
                continue

            # Single-flight across drains: acquire a short-lived lock
            if not self._acquire_lock(envelope.conversation_id):
                # Another drain/worker is processing this conversation; skip
                continue

            try:
                self._run_and_stream_with_retry(envelope)
                # Mark processed regardless of success to avoid hot-looping; on failure we DLQ
                self._mark_processed(envelope.conversation_id, envelope.id)
            finally:
                self._release_lock(envelope.conversation_id)
            processed_conversations.add(envelope.conversation_id)
            processed_count += 1
            last_processed_id = msg.id

        # Only advance our tail to the last processed message id, so skipped messages remain
        self._last_inbound_id = last_processed_id
        return processed_count

    def _run_and_stream(self, envelope: MessageEnvelope) -> None:
        logger = get_json_logger("magent2")
        metrics = get_metrics()
        run_id = str(uuid.uuid4())
        stream_topic = f"stream:{envelope.conversation_id}"

        with use_run_context(run_id, envelope.conversation_id, self._agent_name):
            logger.info(
                "run started",
                extra={
                    "event": "run_started",
                    "run_id": run_id,
                    "conversation_id": envelope.conversation_id,
                    "agent": self._agent_name,
                },
            )
            metrics.increment(
                "runs_started",
                {"agent": self._agent_name, "conversation_id": envelope.conversation_id},
            )
            errored = False
            # Track tool call/error counters for this run (best-effort via Metrics snapshot)
            snap_before = metrics.snapshot()
            tool_calls_before = len([e for e in snap_before if e.get("name") == "tool_calls"]) 
            tool_errors_before = len([e for e in snap_before if e.get("name") == "tool_errors"]) 
            try:
                for event in self._runner.stream_run(envelope):
                    if isinstance(event, BaseStreamEvent):
                        # JSON mode ensures datetimes and other types are serialized safely
                        payload: dict[str, Any] = event.model_dump(mode="json")
                    else:
                        # The runner protocol guarantees dict[str, Any] for non-BaseStreamEvent
                        payload = event
                    self._bus.publish(
                        stream_topic,
                        BusMessage(topic=stream_topic, payload=payload),
                    )
            except Exception:
                errored = True
                logger.exception(
                    "run errored",
                    extra={
                        "event": "run_errored",
                        "run_id": run_id,
                        "conversation_id": envelope.conversation_id,
                        "agent": self._agent_name,
                    },
                )
                metrics.increment(
                    "runs_errored",
                    {"agent": self._agent_name, "conversation_id": envelope.conversation_id},
                )
                # Re-raise so the retry/DLQ policy can handle terminal failures
                raise
            finally:
                if not errored:
                    # Compute delta counts since run start
                    snap_after = metrics.snapshot()
                    tool_calls_after = len(
                        [e for e in snap_after if e.get("name") == "tool_calls"]
                    )
                    tool_errors_after = len(
                        [e for e in snap_after if e.get("name") == "tool_errors"]
                    )
                    run_tool_calls = max(0, tool_calls_after - tool_calls_before)
                    run_tool_errors = max(0, tool_errors_after - tool_errors_before)
                    logger.info(
                        "run completed",
                        extra={
                            "event": "run_completed",
                            "run_id": run_id,
                            "conversation_id": envelope.conversation_id,
                            "agent": self._agent_name,
                            "kv": {"tool_calls": run_tool_calls, "tool_errors": run_tool_errors},
                        },
                    )
                    metrics.increment(
                        "runs_completed",
                        {
                            "agent": self._agent_name,
                            "conversation_id": envelope.conversation_id,
                        },
                    )

    # --- Enhancements for robustness (Issue #38) ---
    def _run_and_stream_with_retry(self, envelope: MessageEnvelope) -> bool:
        """Run with bounded retries and jittered backoff. Publish to DLQ on terminal failure.

        Returns True on success, False on terminal failure.
        """
        max_attempts = 3
        base_sleep = 0.1
        for attempt in range(1, max_attempts + 1):
            try:
                self._run_and_stream(envelope)
                return True
            except Exception:
                # _run_and_stream already logged and incremented metrics on failure
                if attempt >= max_attempts:
                    self._publish_to_dlq(envelope)
                    return False
                # Jittered exponential backoff
                sleep_seconds = min(1.0, base_sleep * (2 ** (attempt - 1)))
                jitter = random.uniform(0.0, 0.05)
                time.sleep(sleep_seconds + jitter)

        # Should not reach here
        return False

    def _publish_to_dlq(self, envelope: MessageEnvelope) -> None:
        """Publish the failed envelope to a dead-letter queue topic.

        Topic: dlq:{agent_name}
        """
        try:
            dlq_topic = f"dlq:{self._agent_name}"
            payload: dict[str, Any] = {
                "event": "dead_letter",
                "agent": self._agent_name,
                "conversation_id": envelope.conversation_id,
                "envelope": envelope.model_dump(mode="json"),
            }
            # Attach a concise error trace if available
            payload["error"] = traceback.format_exc(limit=5)
            self._bus.publish(dlq_topic, BusMessage(topic=dlq_topic, payload=payload))
        except Exception:
            # Best-effort DLQ; do not raise
            get_json_logger("magent2").exception("dlq publish failed")

    def _get_redis_client(self) -> Any | None:
        """Return underlying redis client if bus is a RedisBus, else None."""
        try:
            from magent2.bus.redis_adapter import RedisBus  # local import to avoid hard dep

            if isinstance(self._bus, RedisBus):
                return self._bus._redis
        except Exception:
            return None
        return None

    def _already_processed(self, conversation_id: str, message_id: str) -> bool:
        # Redis-backed idempotency when available
        client = self._get_redis_client()
        key = f"processed:{self._agent_name}:{conversation_id}"
        if client is not None:
            try:
                added = client.sadd(key, message_id)
                # Ensure TTL exists (set if not present)
                try:
                    ttl = int(client.ttl(key))
                except Exception:
                    ttl = -2
                if ttl is None or ttl < 0:
                    client.expire(key, 60 * 60 * 24)
                # If SADD returns 0, it was already present
                return added == 0
            except Exception:
                pass
        # In-memory fallback
        seen = self._processed_by_conversation.setdefault(conversation_id, set())
        if message_id in seen:
            return True
        return False

    def _mark_processed(self, conversation_id: str, message_id: str) -> None:
        client = self._get_redis_client()
        key = f"processed:{self._agent_name}:{conversation_id}"
        if client is not None:
            try:
                client.sadd(key, message_id)
                try:
                    ttl = int(client.ttl(key))
                except Exception:
                    ttl = -2
                if ttl is None or ttl < 0:
                    client.expire(key, 60 * 60 * 24)
                return
            except Exception:
                pass
        self._processed_by_conversation.setdefault(conversation_id, set()).add(message_id)

    def _acquire_lock(self, conversation_id: str) -> bool:
        client = self._get_redis_client()
        key = f"lock:run:{self._agent_name}:{conversation_id}"
        if client is not None:
            try:
                # SET NX EX
                return bool(client.set(key, "1", nx=True, ex=60))
            except Exception:
                pass
        # In-memory fallback
        if conversation_id in self._locks_in_memory:
            return False
        self._locks_in_memory.add(conversation_id)
        return True

    def _release_lock(self, conversation_id: str) -> None:
        client = self._get_redis_client()
        key = f"lock:run:{self._agent_name}:{conversation_id}"
        if client is not None:
            try:
                client.delete(key)
            except Exception:
                pass
        else:
            self._locks_in_memory.discard(conversation_id)

    # (removed duplicate redefinition)
