from __future__ import annotations

import asyncio
import os
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

    def __init__(
        self, agent: Agent, *, session_limit: int = 256, max_turns: int | None = None
    ) -> None:
        self._agent = agent
        self._sessions: dict[str, Any] = {}
        self._session_order: deque[str] = deque()
        self._session_limit = max(1, session_limit)
        self._max_turns: int | None = int(max_turns) if max_turns is not None else None
        # Session configuration (single approach: SQLiteSession if available)
        # - Path is configurable via AGENT_SESSION_PATH; defaults to ./.sessions/agents.db
        self._sqlite_path: str = (
            os.getenv("AGENT_SESSION_PATH") or "./.sessions/agents.db"
        ).strip()

        # Optional persistent sessions (detect availability once)
        self._sqlite_session_cls: Any | None = None
        try:
            from agents import SQLiteSession

            self._sqlite_session_cls = SQLiteSession
        except Exception:
            self._sqlite_session_cls = None

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
    @staticmethod
    def _try_put(
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        item: BaseStreamEvent | dict[str, Any] | None,
    ) -> None:
        try:
            queue.put_nowait(item)
        except Full:
            pass

    def _get_session(self, conversation_id: str) -> Any:
        # Simple LRU: move to end on access, evict from left when over limit
        if conversation_id in self._sessions:
            try:
                self._session_order.remove(conversation_id)
            except ValueError:
                pass
            self._session_order.append(conversation_id)
            return self._sessions[conversation_id]

        # Create a session (SQLite only, fall back to None if unavailable)
        session: Any = self._try_create_sqlite_session(conversation_id)

        self._sessions[conversation_id] = session
        self._session_order.append(conversation_id)
        if len(self._sessions) > self._session_limit:
            evict_id = self._session_order.popleft()
            try:
                del self._sessions[evict_id]
            except KeyError:
                pass
        return session

    # ----------------------------
    # Session creators (best-effort)
    # ----------------------------
    def _try_create_sqlite_session(self, conversation_id: str) -> Any:
        cls = self._sqlite_session_cls
        if cls is None:
            return None
        try:
            # Ensure parent directory exists when using a nested default path
            path = self._sqlite_path or "./.sessions/agents.db"
            self._ensure_dir_for_path(path)
            # SQLiteSession(key: str, path: str)
            return cls(conversation_id, path)
        except Exception:
            return None

    @staticmethod
    def _ensure_dir_for_path(path: str) -> None:
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
        except Exception:
            # Best-effort; if directory cannot be created, the session creation will fail gracefully
            pass

    async def _run_streaming(
        self,
        envelope: MessageEnvelope,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
    ) -> None:
        session = self._get_session(envelope.conversation_id)
        # Kick off the SDK streamed run (pass max_turns when supported)
        if self._max_turns is not None:
            try:
                result_stream = SDKRunner.run_streamed(
                    self._agent,
                    input=envelope.content or "",
                    session=session,
                    max_turns=self._max_turns,
                )
            except TypeError:
                # Older SDK without max_turns parameter
                result_stream = SDKRunner.run_streamed(
                    self._agent,
                    input=envelope.content or "",
                    session=session,
                )
        else:
            result_stream = SDKRunner.run_streamed(
                self._agent,
                input=envelope.content or "",
                session=session,
            )

        token_index = 0
        accumulated_text_parts: list[str] = []

        saw_explicit_output = False
        async for ev in result_stream.stream_events():
            mapped = self._try_map_event(envelope.conversation_id, ev, token_index)
            if mapped is None:
                continue
            inc, saw_output = self._enqueue_mapped_event(queue, mapped, accumulated_text_parts)
            token_index += inc
            if saw_output:
                saw_explicit_output = True

        if not saw_explicit_output:
            # Emit synthetic output only when we actually streamed any token text
            if accumulated_text_parts:
                self._emit_synth_output(queue, envelope.conversation_id, accumulated_text_parts)

    def _map_event(self, conversation_id: str, ev: Any, token_index: int) -> BaseStreamEvent | None:
        """Map SDK stream event to our v1 stream events.

        Tolerant to either typed objects or dict-shaped events.
        """
        ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        data = getattr(ev, "data", None) if not isinstance(ev, dict) else ev.get("data")

        if ev_type == "raw_response_event":
            return self._map_raw_response_event(conversation_id, data, token_index)
        if ev_type == "run_item_stream_event":
            return self._map_run_item_stream_event(conversation_id, data)
        return None

    def _try_map_event(
        self, conversation_id: str, ev: Any, token_index: int
    ) -> BaseStreamEvent | None:
        try:
            return self._map_event(conversation_id, ev, token_index)
        except Exception:
            return None

    def _enqueue_mapped_event(
        self,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        mapped: BaseStreamEvent,
        accumulated_text_parts: list[str],
    ) -> tuple[int, bool]:
        if isinstance(mapped, TokenEvent):
            accumulated_text_parts.append(mapped.text)
            OpenAIAgentsRunner._try_put(queue, mapped)
            return 1, False
        if isinstance(mapped, ToolStepEvent):
            OpenAIAgentsRunner._try_put(queue, mapped)
            return 0, False
        if isinstance(mapped, OutputEvent):
            OpenAIAgentsRunner._try_put(queue, mapped)
            return 0, True
        return 0, False

    def _emit_synth_output(
        self,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        conversation_id: str,
        accumulated_text_parts: list[str],
    ) -> None:
        final_text = "".join(accumulated_text_parts)
        OpenAIAgentsRunner._try_put(
            queue, OutputEvent(conversation_id=conversation_id, text=final_text)
        )

    # Note: log emission to stream is intentionally omitted to keep event order stable for tests.

    @staticmethod
    def _map_raw_response_event(
        conversation_id: str, data: Any, token_index: int
    ) -> TokenEvent | None:
        if isinstance(data, ResponseTextDeltaEvent):
            delta = getattr(data, "delta", None)
            if isinstance(delta, str) and delta:
                return TokenEvent(conversation_id=conversation_id, text=delta, index=token_index)
            return None
        if isinstance(data, dict):
            delta_val = data.get("delta")
            if isinstance(delta_val, str) and delta_val:
                return TokenEvent(
                    conversation_id=conversation_id, text=delta_val, index=token_index
                )
        return None

    def _map_run_item_stream_event(self, conversation_id: str, item: Any) -> BaseStreamEvent | None:
        if item is None:
            return None
        name, args, result = self._parse_name_args_result(item)
        tool_invocation = self._map_tool_invocation(conversation_id, name, args)
        if tool_invocation is not None:
            return tool_invocation
        # Prefer explicit final output if present or embedded in result dict
        # First, check if the whole item is a final message
        text_value = self._extract_text(item)
        if self._is_final_item(item) and isinstance(text_value, str) and text_value:
            return OutputEvent(
                conversation_id=conversation_id,
                text=text_value,
                usage=self._extract_usage(item),
            )
        # Next, handle result dict carrying final text/usage
        tool_result = self._map_tool_result_or_output(conversation_id, name, result)
        if tool_result is not None and isinstance(tool_result, OutputEvent):
            return tool_result
        # Otherwise map tool invocation/result events
        if tool_invocation is not None:
            return tool_invocation
        if tool_result is not None:
            return tool_result
        return None

    @staticmethod
    def _parse_name_args_result(item: Any) -> tuple[Any, Any, Any]:
        return (
            OpenAIAgentsRunner._get_name(item),
            OpenAIAgentsRunner._get_args(item),
            OpenAIAgentsRunner._get_result(item),
        )

    @staticmethod
    def _get_name(item: Any) -> Any:
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            return item.get("name") or item.get("tool_name")
        return name

    @staticmethod
    def _get_args(item: Any) -> Any:
        args = getattr(item, "arguments", None)
        if args is None and isinstance(item, dict):
            return item.get("arguments") or item.get("args")
        return args

    @staticmethod
    def _get_result(item: Any) -> Any:
        result = getattr(item, "result", None)
        if result is None and isinstance(item, dict):
            return item.get("result") or item.get("output") or item.get("content")
        return result

    @staticmethod
    def _map_tool_invocation(conversation_id: str, name: Any, args: Any) -> ToolStepEvent | None:
        if isinstance(name, str) and name and (isinstance(args, dict) or isinstance(args, list)):
            return ToolStepEvent(
                conversation_id=conversation_id,
                name=name,
                args=OpenAIAgentsRunner._normalize_args(
                    args if isinstance(args, (dict, list)) else {}
                ),
            )
        return None

    @staticmethod
    def _map_tool_result_or_output(
        conversation_id: str, name: Any, result: Any
    ) -> BaseStreamEvent | None:
        # If result is a dict with final output fields, emit OutputEvent with usage
        if isinstance(result, dict):
            text = OpenAIAgentsRunner._extract_text(result)
            if isinstance(text, str) and text:
                return OutputEvent(
                    conversation_id=conversation_id,
                    text=text,
                    usage=OpenAIAgentsRunner._extract_usage(result),
                )
            return None
        # Otherwise summarize tool result
        if isinstance(name, str) and name and result is not None:
            return ToolStepEvent(
                conversation_id=conversation_id,
                name=name,
                args={},
                result_summary=OpenAIAgentsRunner._summarize(result),
            )
        return None

    @staticmethod
    def _normalize_args(args: dict[str, Any] | list[Any]) -> dict[str, Any]:
        if isinstance(args, dict):
            return args
        return {"args": args}

    @staticmethod
    def _is_final_item(item: Any) -> bool:
        # Attribute flags
        for flag_attr in ("final", "is_final", "completed"):
            if getattr(item, flag_attr, False):
                return True
        # Dict flags
        if isinstance(item, dict):
            if any(
                item.get(k) in (True, "completed", "done", "final")
                for k in ("final", "is_final", "completed", "status")
            ):
                return True
            kind = item.get("kind") or item.get("type") or ""
            if isinstance(kind, str) and "completed" in kind:
                return True
        return False

    @staticmethod
    def _summarize(value: Any, *, limit: int = 200) -> str:
        text = str(value)
        return text if len(text) <= limit else text[: limit - 1] + "\u2026"

    @staticmethod
    def _extract_text(item: Any) -> str | None:
        text = OpenAIAgentsRunner._extract_text_from_attrs(item)
        if text:
            return text
        if isinstance(item, dict):
            return OpenAIAgentsRunner._extract_text_from_dict(item)
        return None

    @staticmethod
    def _extract_text_from_attrs(item: Any) -> str | None:
        for attr in ("text", "content", "message", "output"):
            val = getattr(item, attr, None)
            if isinstance(val, str) and val:
                return val
        return None

    @staticmethod
    def _extract_text_from_dict(dct: dict[str, Any]) -> str | None:
        # Fast-path direct string values
        for key in ("text", "content", "message", "output"):
            val = dct.get(key)
            if isinstance(val, str) and val:
                return val
        # Handle list values by concatenating string-like parts
        for key in ("text", "content", "message", "output"):
            val = dct.get(key)
            if isinstance(val, list):
                parts = OpenAIAgentsRunner._collect_string_parts(val)
                if parts:
                    return "".join(parts)
        return None

    @staticmethod
    def _collect_string_parts(items: list[Any]) -> list[str]:
        parts: list[str] = []
        for item in items:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or item.get("output")
                if isinstance(t, str):
                    parts.append(t)
        return parts

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
