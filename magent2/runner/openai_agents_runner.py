from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Iterable
from queue import Full, Queue
from typing import Any

from agents import Agent
from agents import Runner as SDKRunner
from openai.types.responses import ResponseTextDeltaEvent

from magent2.models.envelope import (
    BaseStreamEvent,
    MessageEnvelope,
    OutputEvent,
    TokenEvent,
    ToolStepEvent,
)


class OpenAIAgentsRunner:
    """Adapter that bridges the async OpenAI Agents SDK stream to our sync Worker protocol.

    - Maintains simple LRU of sessions keyed by conversation_id
    - Maps SDK events to v1 stream events (TokenEvent, ToolStepEvent, OutputEvent)
    - Returns a synchronous iterator suitable for the existing Worker loop
    """

    def __init__(self, agent: Agent, *, session_limit: int = 256) -> None:
        self._agent = agent
        self._sessions: dict[str, Any] = {}
        self._session_order: deque[str] = deque()
        self._session_limit = max(1, session_limit)

    # ----------------------------
    # Public API (Runner protocol)
    # ----------------------------
    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        # Bound the queue to guard against unbounded growth if a consumer is slow
        events_queue: Queue[BaseStreamEvent | dict[str, Any] | None] = Queue(maxsize=1024)
        sentinel: None = None

        def _runner() -> None:
            try:
                asyncio.run(self._run_streaming(envelope, events_queue))
            finally:
                # Signal completion regardless of errors
                try:
                    events_queue.put_nowait(sentinel)
                except Full:
                    # Best-effort: if queue is full, iterator will finish when the queue is drained
                    pass

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

        # Drain the queue until sentinel is received
        while True:
            item = events_queue.get()
            if item is sentinel:
                break
            # mypy: item cannot be None here, guarded by sentinel check
            assert item is not None
            yield item

    # ----------------------------
    # Internal helpers
    # ----------------------------
    def _get_session(self, conversation_id: str) -> Any:
        # Simple LRU: move to end on access, evict from left when over limit
        if conversation_id in self._sessions:
            try:
                self._session_order.remove(conversation_id)
            except ValueError:
                pass
            self._session_order.append(conversation_id)
            return self._sessions[conversation_id]

        # Create a new opaque session object; if SDK exposes a concrete Session,
        # callers/tests can still verify reuse by identity.
        session: Any = object()
        self._sessions[conversation_id] = session
        self._session_order.append(conversation_id)
        if len(self._sessions) > self._session_limit:
            evict_id = self._session_order.popleft()
            try:
                del self._sessions[evict_id]
            except KeyError:
                pass
        return session

    async def _run_streaming(
        self,
        envelope: MessageEnvelope,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
    ) -> None:
        session = self._get_session(envelope.conversation_id)
        # Kick off the SDK streamed run
        result_stream = SDKRunner.run_streamed(
            self._agent,
            input=envelope.content or "",
            session=session,
        )

        token_index = 0
        accumulated_text_parts: list[str] = []

        saw_explicit_output = False
        async for ev in result_stream.stream_events():
            try:
                mapped = self._map_event(envelope.conversation_id, ev, token_index)
            except Exception:
                # Skip malformed/unknown events without killing the stream
                continue
            if mapped is None:
                continue
            if isinstance(mapped, TokenEvent):
                token_index += 1
                accumulated_text_parts.append(mapped.text)
                try:
                    queue.put_nowait(mapped)
                except Full:
                    # Drop tokens under backpressure; final OutputEvent will still summarize
                    pass
            elif isinstance(mapped, ToolStepEvent):
                try:
                    queue.put_nowait(mapped)
                except Full:
                    pass
            elif isinstance(mapped, OutputEvent):
                # If the SDK yields a final output explicitly, prefer it
                try:
                    queue.put_nowait(mapped)
                except Full:
                    pass
                saw_explicit_output = True

        # Ensure we always emit a final OutputEvent if not already provided
        if not saw_explicit_output:
            final_text = "".join(accumulated_text_parts)
            try:
                queue.put_nowait(
                    OutputEvent(
                        conversation_id=envelope.conversation_id,
                        text=final_text,
                    )
                )
            except Full:
                # If backpressure is extreme, we still terminate via sentinel
                pass

    def _map_event(self, conversation_id: str, ev: Any, token_index: int) -> BaseStreamEvent | None:
        """Map SDK stream event to our v1 stream events.

        Tolerant to either typed objects or dict-shaped events.
        """
        ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        data = getattr(ev, "data", None) if not isinstance(ev, dict) else ev.get("data")

        # 1) raw model token deltas
        if ev_type == "raw_response_event":
            # data can be a typed ResponseTextDeltaEvent or a dict-like with key 'delta'
            if isinstance(data, ResponseTextDeltaEvent):
                delta = getattr(data, "delta", None)
                if isinstance(delta, str) and delta:
                    return TokenEvent(
                        conversation_id=conversation_id, text=delta, index=token_index
                    )
                return None
            if isinstance(data, dict):
                delta_val = data.get("delta")
                if isinstance(delta_val, str) and delta_val:
                    return TokenEvent(
                        conversation_id=conversation_id, text=delta_val, index=token_index
                    )
            return None

        # 2) run item events: tools, messages, etc.
        if ev_type == "run_item_stream_event":
            item = data
            # Heuristics for tool invocation
            if item is not None:
                # Prefer attribute access, fallback to dict keys
                name = getattr(item, "name", None)
                if name is None and isinstance(item, dict):
                    name = item.get("name") or item.get("tool_name")

                args = getattr(item, "arguments", None)
                if args is None and isinstance(item, dict):
                    args = item.get("arguments") or item.get("args")

                result = getattr(item, "result", None)
                if result is None and isinstance(item, dict):
                    result = item.get("result") or item.get("output") or item.get("content")

                # If we have a tool invocation (name + args)
                if isinstance(name, str) and name and isinstance(args, dict | list):
                    # Normalize args to dict where possible
                    norm_args: dict[str, Any]
                    if isinstance(args, dict):
                        norm_args = args
                    else:
                        # If args is a list or other type, coerce minimally
                        norm_args = {"args": args}
                    return ToolStepEvent(conversation_id=conversation_id, name=name, args=norm_args)

                # If we have a tool result/completion
                if isinstance(name, str) and name and result is not None:
                    summary = self._summarize(result)
                    return ToolStepEvent(
                        conversation_id=conversation_id,
                        name=name,
                        args={},
                        result_summary=summary,
                    )

                # Detect explicit final assistant output
                is_final = False
                # Attribute flags
                for flag_attr in ("final", "is_final", "completed"):
                    if getattr(item, flag_attr, False):
                        is_final = True
                        break
                # Dict flags
                if isinstance(item, dict):
                    if any(
                        item.get(k) in (True, "completed", "done", "final")
                        for k in ("final", "is_final", "completed", "status")
                    ):
                        is_final = True
                    kind = item.get("kind") or item.get("type") or ""
                    if isinstance(kind, str) and "completed" in kind:
                        is_final = True

                text_value = self._extract_text(item)
                if is_final and isinstance(text_value, str) and text_value:
                    usage = self._extract_usage(item)
                    return OutputEvent(
                        conversation_id=conversation_id,
                        text=text_value,
                        usage=usage,
                    )

            return None

        # Ignore other event kinds for now
        return None

    @staticmethod
    def _summarize(value: Any, *, limit: int = 200) -> str:
        text = str(value)
        return text if len(text) <= limit else text[: limit - 1] + "\u2026"

    @staticmethod
    def _extract_text(item: Any) -> str | None:
        # Attribute possibilities
        for attr in ("text", "content", "message", "output"):
            val = getattr(item, attr, None)
            if isinstance(val, str) and val:
                return val
        # Dict possibilities
        if isinstance(item, dict):
            for key in ("text", "content", "message", "output"):
                val = item.get(key)
                if isinstance(val, str) and val:
                    return val
                if isinstance(val, list):
                    # Concatenate string-like pieces
                    parts: list[str] = []
                    for p in val:
                        if isinstance(p, str):
                            parts.append(p)
                        elif isinstance(p, dict):
                            t = p.get("text") or p.get("content") or p.get("output")
                            if isinstance(t, str):
                                parts.append(t)
                    if parts:
                        return "".join(parts)
        return None

    @staticmethod
    def _extract_usage(item: Any) -> dict[str, Any] | None:
        usage = getattr(item, "usage", None)
        if isinstance(usage, dict):
            return usage
        if isinstance(item, dict):
            u = item.get("usage")
            if isinstance(u, dict):
                return u
        return None


__all__ = ["OpenAIAgentsRunner"]
