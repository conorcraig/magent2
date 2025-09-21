from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from collections.abc import Iterable
from queue import Full, Queue
from typing import Any, cast

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
from magent2.observability import get_json_logger, get_run_context, use_run_context


def _dbg_enabled() -> bool:
    try:
        return os.getenv("RUNNER_DEBUG_EVENTS", "0").strip() == "1"
    except Exception:
        return False


def _dbg_log(message: str, extra: dict[str, Any] | None = None) -> None:
    if not _dbg_enabled():
        return
    try:
        get_json_logger("magent2.runner").info(message, extra=extra or {})
    except Exception:
        pass


class OpenAIAgentsRunner:
    """Adapter that bridges the async OpenAI Agents SDK stream to our sync Worker protocol.

    - Maintains simple LRU of sessions keyed by conversation_id
    - Maps SDK events to v1 stream events (TokenEvent, ToolStepEvent, OutputEvent)
    - Returns a synchronous iterator suitable for the existing Worker loop

    Tool lifecycle mapping (Agents SDK specifics):
    - Agents streaming emits `ToolCallItem` (often missing id/name) and later `ToolCallOutputItem`
      (carries `call_id` and output; name can still be missing). To provide timely UX feedback and
      reliable correlation:
      * On `ToolCallItem` we emit ToolStepEvent(start) immediately using a synthetic id; we record
        start_ns and best-effort name for backfill.
      * On `ToolCallOutputItem` we correlate via FIFO to the earliest pending id and emit the
        matching ToolStepEvent(success/error) with computed duration and the same id. If no pending
        start exists, we emit a fallback start+success pair at output time to preserve lifecycle.
    - This design keeps the frontend contract simple and consistent despite SDK metadata gaps.
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
        # Track tool starts to compute durations when results arrive
        self._tool_start_ns: dict[tuple[str, str], int] = {}
        # Track tool names by call id to backfill names on result events
        self._tool_name_by_id: dict[tuple[str, str], str] = {}
        # Pending starts when ToolCallItem lacks an id; keyed by conversation
        self._pending_tool_starts: dict[str, list[int]] = {}
        # Best-effort pending tool names captured from ToolCallItem in FIFO order
        self._pending_tool_names: dict[str, list[str]] = {}
        # FIFO of synthetic tool_call_ids for immediate-start correlation
        self._pending_tool_ids: dict[str, list[str]] = {}

    # ----------------------------
    # Public API (Runner protocol)
    # ----------------------------
    def stream_run(self, envelope: MessageEnvelope) -> Iterable[BaseStreamEvent | dict[str, Any]]:
        # Bound the queue to guard against unbounded growth if a consumer is slow
        events_queue: Queue[BaseStreamEvent | dict[str, Any] | None] = Queue(maxsize=1024)
        sentinel: None = None
        # Capture run context from the caller thread (Worker) to propagate into our runner thread
        parent_ctx = get_run_context() or {}

        def _runner() -> None:
            try:
                run_id = str(parent_ctx.get("run_id")) if parent_ctx.get("run_id") else None
                conv_id = (
                    str(parent_ctx.get("conversation_id"))
                    if parent_ctx.get("conversation_id")
                    else None
                )
                agent = str(parent_ctx.get("agent")) if parent_ctx.get("agent") else None
                if run_id and conv_id:
                    with use_run_context(run_id, conv_id, agent):
                        asyncio.run(self._run_streaming(envelope, events_queue))
                else:
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
        self._debug_run_streaming_start(envelope.conversation_id)

        session = self._get_session(envelope.conversation_id)
        result_stream = self._create_result_stream(envelope, session)

        token_index = 0
        accumulated_text_parts: list[str] = []
        saw_explicit_output = False

        self._debug_before_event_loop(envelope.conversation_id)

        token_index, saw_explicit_output = await self._process_event_stream(
            result_stream,
            envelope.conversation_id,
            queue,
            token_index,
            accumulated_text_parts,
            saw_explicit_output,
        )

        if not saw_explicit_output:
            self._emit_synth_output(queue, envelope.conversation_id, accumulated_text_parts)

    def _debug_run_streaming_start(self, conversation_id: str) -> None:
        """Log debug information when run streaming starts."""
        _dbg_log(
            "DEBUG: _run_streaming started",
            extra={
                "event": "run_item_debug",
                "service": "runner",
                "conversation_id": conversation_id,
            },
        )

    def _create_result_stream(self, envelope: MessageEnvelope, session: Any) -> Any:
        """Create the SDK result stream with appropriate parameters."""
        if self._max_turns is not None:
            try:
                return SDKRunner.run_streamed(
                    self._agent,
                    input=envelope.content or "",
                    session=session,
                    max_turns=self._max_turns,
                )
            except TypeError:
                # Older SDK without max_turns parameter
                pass

        return SDKRunner.run_streamed(
            self._agent,
            input=envelope.content or "",
            session=session,
        )

    def _debug_before_event_loop(self, conversation_id: str) -> None:
        """Log debug information before starting the event processing loop."""
        _dbg_log(
            "DEBUG: about to start async for loop",
            extra={
                "event": "run_item_debug",
                "service": "runner",
                "conversation_id": conversation_id,
            },
        )

    async def _process_event_stream(
        self,
        result_stream: Any,
        conversation_id: str,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        token_index: int,
        accumulated_text_parts: list[str],
        saw_explicit_output: bool,
    ) -> tuple[int, bool]:
        """Process the async event stream and return updated token_index and saw_output flag."""
        current_token_index = token_index
        current_saw_output = saw_explicit_output

        async for ev in result_stream.stream_events():
            self._debug_sdk_event(ev)
            mapped = self._try_map_event(conversation_id, ev, current_token_index)
            if mapped is None:
                continue

            inc, saw_output = self._enqueue_mapped_event(queue, mapped, accumulated_text_parts)
            current_token_index += inc
            if saw_output:
                current_saw_output = True

        return current_token_index, current_saw_output

    def _debug_sdk_event(self, ev: Any) -> None:
        """Log SDK event type for observability."""
        try:
            et = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
            _dbg_log(
                f"sdk stream event type={et or ''}",
                extra={"event": "sdk_event", "type": et or "", "service": "runner"},
            )
        except Exception:
            pass

    def _map_event(
        self, conversation_id: str, ev: Any, token_index: int
    ) -> BaseStreamEvent | list[BaseStreamEvent] | None:
        """Map SDK stream event to our v1 stream events.

        Tolerant to either typed objects or dict-shaped events.
        """
        self._debug_log_event(conversation_id, ev)

        ev_type, data = self._extract_event_type_and_data(ev)

        if ev_type == "raw_response_event":
            return self._map_raw_response_event(conversation_id, data, token_index)
        if ev_type == "run_item_stream_event":
            return self._map_run_item_stream_event_for_item(conversation_id, ev, data)
        if self._is_response_tool_call_event(ev_type):
            return self._map_response_tool_event(conversation_id, cast(str, ev_type), data)
        return None

    def _debug_log_event(self, conversation_id: str, ev: Any) -> None:
        """Log debug information for event processing."""
        _dbg_log(
            "DEBUG: _map_event called",
            extra={
                "event": "run_item_debug",
                "service": "runner",
                "conversation_id": conversation_id,
            },
        )

        ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        data = getattr(ev, "data", None) if not isinstance(ev, dict) else ev.get("data")

        msg = (
            f"DEBUG: processing event, ev_type={ev_type}, "
            f"ev_class={type(ev).__name__}, has_data={data is not None}"
        )
        _dbg_log(
            msg,
            extra={
                "event": "run_item_debug",
                "service": "runner",
                "ev_type": ev_type,
                "ev_class": type(ev).__name__,
            },
        )

    @staticmethod
    def _extract_event_type_and_data(ev: Any) -> tuple[str | None, Any]:
        """Extract event type and data from event object."""
        ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        data = getattr(ev, "data", None) if not isinstance(ev, dict) else ev.get("data")
        return ev_type, data

    def _map_run_item_stream_event_for_item(
        self, conversation_id: str, ev: Any, data: Any
    ) -> BaseStreamEvent | list[BaseStreamEvent] | None:
        """Map run_item_stream_event by extracting the item."""
        _dbg_log(
            "routing run_item_stream_event to _map_run_item_stream_event",
            extra={"event": "run_item_debug", "service": "runner"},
        )
        # Prefer explicit 'item' attribute per SDK examples; fall back to data
        item = getattr(ev, "item", None)
        if item is None and isinstance(ev, dict):
            item = ev.get("item")
        if item is None:
            item = data
        return self._map_run_item_stream_event(conversation_id, item)

    @staticmethod
    def _is_response_tool_call_event(ev_type: str | None) -> bool:
        """Check if event type is a response tool call event."""
        return isinstance(ev_type, str) and ev_type.startswith("response.tool_call.")

    def _try_map_event(
        self, conversation_id: str, ev: Any, token_index: int
    ) -> BaseStreamEvent | list[BaseStreamEvent] | None:
        try:
            return self._map_event(conversation_id, ev, token_index)
        except Exception:
            return None

    def _enqueue_mapped_event(
        self,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        mapped: BaseStreamEvent | list[BaseStreamEvent],
        accumulated_text_parts: list[str],
    ) -> tuple[int, bool]:
        # Handle multiple events emitted for a single SDK item
        if isinstance(mapped, list):
            total_inc = 0
            saw_output_any = False
            for ev in mapped:
                inc, saw_output = self._enqueue_mapped_event(queue, ev, accumulated_text_parts)
                total_inc += inc
                saw_output_any = saw_output_any or saw_output
            return total_inc, saw_output_any
        if isinstance(mapped, TokenEvent):
            accumulated_text_parts.append(mapped.text)
            try:
                queue.put_nowait(mapped)
            except Full:
                pass
            return 1, False
        if isinstance(mapped, ToolStepEvent):
            try:
                queue.put_nowait(mapped)
            except Full:
                pass
            return 0, False
        if isinstance(mapped, OutputEvent):
            try:
                queue.put_nowait(mapped)
            except Full:
                pass
            return 0, True
        return 0, False

    def _emit_synth_output(
        self,
        queue: Queue[BaseStreamEvent | dict[str, Any] | None],
        conversation_id: str,
        accumulated_text_parts: list[str],
    ) -> None:
        final_text = "".join(accumulated_text_parts)
        try:
            queue.put_nowait(OutputEvent(conversation_id=conversation_id, text=final_text))
        except Full:
            pass

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

    def _map_run_item_stream_event(
        self, conversation_id: str, item: Any
    ) -> BaseStreamEvent | list[BaseStreamEvent] | None:
        if item is None:
            return None

        item_type, name, args, result = self._extract_item_details(item)
        self._debug_log_item_mapping(item_type, name, args, result)

        if not self._is_valid_name(name):
            self._log_tool_name_missing(item)

        # Try different mapping strategies in order
        return (
            self._try_map_typed_tool_item(conversation_id, item_type, name, args, result, item)
            or self._map_tool_invocation(conversation_id, name, args)
            or self._map_tool_result(conversation_id, name, result)
            or self._try_map_tool_error(conversation_id, item, name)
            or self._map_final_output_event(conversation_id, item)
        )

    def _extract_item_details(self, item: Any) -> tuple[str, Any, Any, Any]:
        """Extract item type, name, args, and result from item."""
        item_type = self._get_item_type_str(item)
        name, args, result = self._parse_name_args_result(item)
        return item_type, name, args, result

    def _debug_log_item_mapping(self, item_type: str, name: Any, args: Any, result: Any) -> None:
        """Log debug information for item mapping."""
        _dbg_log(
            f"_map_run_item_stream_event mapping item_type={item_type}",
            extra={"event": "run_item_debug", "service": "runner"},
        )

        msg2 = (
            f"run_item_stream_event item_type={item_type} name={name} "
            f"has_args={args is not None} has_result={result is not None}"
        )
        _dbg_log(msg2, extra={"event": "run_item_debug", "service": "runner"})

    @staticmethod
    def _is_valid_name(name: Any) -> bool:
        """Check if name is a valid non-empty string."""
        return isinstance(name, str) and bool(name)

    def _try_map_typed_tool_item(
        self, conversation_id: str, item_type: str, name: Any, args: Any, result: Any, item: Any
    ) -> BaseStreamEvent | list[BaseStreamEvent] | None:
        """Try to map as a typed tool item."""
        typed = self._map_typed_tool_item(conversation_id, item_type, name, args, result, item)
        if typed is not None:
            # typed is ToolStepEvent | list[ToolStepEvent]; both conform to BaseStreamEvent variants
            return typed  # type: ignore[return-value]
        return None

    def _try_map_tool_error(
        self, conversation_id: str, item: Any, name: Any
    ) -> ToolStepEvent | None:
        """Try to map tool error if name is valid."""
        err = self._extract_error(item)
        if isinstance(err, str) and self._is_valid_name(name):
            call_id = self._get_tool_call_id(item) or self._gen_tool_call_id()
            return ToolStepEvent(
                conversation_id=conversation_id,
                name=name,
                args={},
                status="error",
                error=self._summarize(err, limit=160),
                tool_call_id=call_id,
            )
        return None

    def _log_tool_name_missing(self, item: Any) -> None:
        """Log a concise diagnostic when tool name cannot be extracted.

        This improves debuggability without complicating the main flow.
        """
        try:
            tool_obj = getattr(item, "tool", None)
            tool_name = getattr(tool_obj, "name", None) if tool_obj is not None else None
            raw = getattr(item, "raw_item", None)
            raw_keys = list(raw.keys())[:5] if isinstance(raw, dict) else None
            msg = (
                "tool_name_missing diag "
                f"has_tool={tool_obj is not None} tool_name={tool_name} "
                f"has_raw_item={isinstance(raw, dict)} raw_keys={raw_keys}"
            )
            _dbg_log(msg, extra={"event": "run_item_debug", "service": "runner"})
        except Exception:
            pass

    def _map_response_tool_event(
        self, conversation_id: str, ev_type: str, data: Any
    ) -> ToolStepEvent | None:
        # Expected shapes from Responses API streaming for tools
        # created → start; completed → success; failed → error
        event_data = self._extract_response_event_data(data)
        if not event_data.name_val:
            return None

        if ev_type.endswith(".created"):
            return self._create_tool_start_event(conversation_id, event_data)
        if ev_type.endswith(".completed"):
            return self._create_tool_success_event(conversation_id, event_data)
        if ev_type.endswith((".failed", ".error")):
            return self._create_tool_error_event(conversation_id, event_data)

        # Ignore deltas and unknown subtypes
        return None

    @staticmethod
    def _extract_response_event_data(data: Any) -> Any:
        """Extract and structure event data for response tool events."""
        from collections import namedtuple

        ResponseEventData = namedtuple(
            "ResponseEventData", ["call_id", "name_val", "args_preview", "data_dict"]
        )

        d = data if isinstance(data, dict) else {}
        call_id = d.get("id") if isinstance(d.get("id"), str) else None
        name_val = d.get("name") if isinstance(d.get("name"), str) else None
        args_val = d.get("arguments")

        # Build a minimal, safe args preview. For known terminal tools, extract command/cwd.
        def _minimal_args_preview(tool_name: str | None, arguments: Any) -> dict[str, Any]:
            try:
                is_terminal = isinstance(tool_name, str) and tool_name in (
                    "terminal_run_tool",
                    "terminal_run",
                    "terminal.run",
                    "terminal.run_tool",
                )
                parsed: dict[str, Any] | None = None
                if isinstance(arguments, str):
                    if is_terminal:
                        parsed = json.loads(arguments)
                elif isinstance(arguments, dict):
                    parsed = arguments
                if isinstance(parsed, dict):
                    cmd = parsed.get("command")
                    cwd = parsed.get("cwd")
                    out: dict[str, Any] = {}
                    if isinstance(cmd, str) and cmd:
                        out["command"] = cmd if len(cmd) <= 160 else (cmd[:157] + "...")
                    if isinstance(cwd, str) and cwd:
                        out["cwd"] = cwd
                    if out:
                        return out
                # Fallback for non-terminal or unparsed args
                if isinstance(arguments, str):
                    return {"len": len(arguments)}
                if isinstance(arguments, dict):
                    try:
                        return {"keys": list(arguments.keys())[:5]}
                    except Exception:
                        return {}
            except Exception:
                pass
            return {}

        args_preview = _minimal_args_preview(name_val, args_val)

        return ResponseEventData(call_id, name_val, args_preview, d)

    def _create_tool_start_event(self, conversation_id: str, event_data: Any) -> ToolStepEvent:
        """Create a tool start event."""
        call_id = event_data.call_id or self._gen_tool_call_id()
        self._tool_start_ns[(conversation_id, call_id)] = self._now_ns()
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=event_data.name_val,
            args=event_data.args_preview,
            status="start",
            tool_call_id=call_id,
        )

    def _create_tool_success_event(self, conversation_id: str, event_data: Any) -> ToolStepEvent:
        """Create a tool success event."""
        call_id = event_data.call_id or self._gen_tool_call_id()
        start_ns = self._tool_start_ns.pop((conversation_id, call_id), None)
        dur_ms: int | None = None
        if isinstance(start_ns, int):
            dur_ms = int((self._now_ns() - start_ns) / 1_000_000)
        result_val = event_data.data_dict.get("result")
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=event_data.name_val,
            args=event_data.args_preview or {},
            result_summary=self._summarize(result_val) if result_val is not None else None,
            status="success",
            duration_ms=dur_ms,
            tool_call_id=call_id,
        )

    def _create_tool_error_event(self, conversation_id: str, event_data: Any) -> ToolStepEvent:
        """Create a tool error event."""
        call_id = event_data.call_id or self._gen_tool_call_id()
        err_text = (
            event_data.data_dict.get("error") or event_data.data_dict.get("message") or "tool error"
        )
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=event_data.name_val,
            args=event_data.args_preview or {},
            status="error",
            error=self._summarize(err_text, limit=160),
            tool_call_id=call_id,
        )

    @staticmethod
    def _get_item_type_str(item: Any) -> str:
        t = getattr(item, "type", None)
        if t is None and isinstance(item, dict):
            t = item.get("type") or item.get("kind")
        return str(t or "")

    def _map_typed_tool_item(
        self, conversation_id: str, item_type: str, name: Any, args: Any, result: Any, item: Any
    ) -> ToolStepEvent | list[ToolStepEvent] | None:
        """Map typed tool items from Agents SDK streaming.

        Policy:
        - tool_call → emit start immediately (synthetic id);
          remember start_ns/name; enqueue id FIFO.
        - tool_call_output → pop earliest pending id; emit success/error with same id and duration.
          If no pending id exists, emit a fallback start+success pair at output time.
        """
        t = item_type.lower()
        if not t:
            return None

        if self._is_tool_call(t):
            return self._handle_tool_call(conversation_id, name, args)
        if self._is_tool_result(t):
            return self._handle_tool_result(conversation_id, name, result, item)

        return None

    def _handle_tool_call(self, conversation_id: str, name: Any, args: Any) -> ToolStepEvent:
        """Handle tool call by emitting start event with synthetic ID."""
        call_id = self._gen_tool_call_id()
        self._pending_tool_ids.setdefault(conversation_id, []).append(call_id)
        self._tool_start_ns[(conversation_id, call_id)] = self._now_ns()
        final_name = name if isinstance(name, str) and name else "unknown_tool"
        self._tool_name_by_id[(conversation_id, call_id)] = final_name
        normalized_args = self._normalize_args(args) if isinstance(args, dict | list) else {}
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=final_name,
            args=normalized_args,
            status="start",
            tool_call_id=call_id,
        )

    def _handle_tool_result(
        self, conversation_id: str, name: Any, result: Any, item: Any
    ) -> ToolStepEvent | list[ToolStepEvent] | None:
        """Handle tool result by correlating with pending ID or creating fallback."""
        pending_id = self._get_pending_tool_id(conversation_id)

        if pending_id is None:
            return self._create_fallback_tool_events(conversation_id, name, result, item)

        stored_name = self._tool_name_by_id.get((conversation_id, pending_id))
        final_name = name if isinstance(name, str) and name else (stored_name or "unknown_tool")
        return self._build_tool_result_event(
            conversation_id, final_name, result, item, call_id_override=pending_id
        )

    def _get_pending_tool_id(self, conversation_id: str) -> str | None:
        """Get the next pending tool ID for correlation."""
        id_list = self._pending_tool_ids.get(conversation_id)
        if isinstance(id_list, list) and id_list:
            pending_id = id_list.pop(0)
            if not id_list:
                self._pending_tool_ids.pop(conversation_id, None)
            return pending_id
        return None

    def _create_fallback_tool_events(
        self, conversation_id: str, name: Any, result: Any, item: Any
    ) -> ToolStepEvent | list[ToolStepEvent] | None:
        """Create fallback start+success events when no pending ID exists."""
        fallback_id = self._gen_tool_call_id()
        self._tool_start_ns[(conversation_id, fallback_id)] = self._now_ns()
        provisional_name = name if isinstance(name, str) and name else "unknown_tool"
        self._tool_name_by_id[(conversation_id, fallback_id)] = provisional_name

        start_event = ToolStepEvent(
            conversation_id=conversation_id,
            name=provisional_name,
            args={},
            status="start",
            tool_call_id=fallback_id,
        )

        success_event = self._build_tool_result_event(
            conversation_id, provisional_name, result, item, call_id_override=fallback_id
        )

        if success_event is None:
            return start_event
        return [start_event, success_event]

    @staticmethod
    def _is_tool_call(t: str) -> bool:
        return ("tool_call" in t) and ("output" not in t)

    @staticmethod
    def _is_tool_result(t: str) -> bool:
        return (
            ("tool_call_output" in t)
            or ("tool" in t and "output" in t)
            or ("tool_call_output_item" in t)
        )

    def _build_tool_call_event(
        self, conversation_id: str, name: Any, args: Any, item: Any
    ) -> ToolStepEvent | None:
        if not (isinstance(name, str) and name):
            # Fallback: emit with generic tool name to preserve lifecycle visibility
            name = "unknown_tool"
        normalized_args = self._normalize_args(args) if isinstance(args, dict | list) else {}
        call_id = self._get_tool_call_id(item) or self._gen_tool_call_id()
        self._tool_start_ns[(conversation_id, call_id)] = self._now_ns()
        # Remember tool name for backfilling on success
        if isinstance(name, str) and name:
            self._tool_name_by_id[(conversation_id, call_id)] = name
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=name,
            args=normalized_args,
            status="start",
            tool_call_id=call_id,
        )

    def _build_tool_result_event(
        self,
        conversation_id: str,
        name: Any,
        result: Any,
        item: Any,
        *,
        call_id_override: str | None = None,
    ) -> ToolStepEvent | None:
        call_id = call_id_override or self._get_tool_call_id(item) or self._gen_tool_call_id()
        # If name missing, backfill from prior start event
        final_name = (
            name
            if isinstance(name, str) and name
            else self._tool_name_by_id.get((conversation_id, call_id))
        )
        if not (isinstance(final_name, str) and final_name):
            final_name = "unknown_tool"
        summary = self._summarize(result)
        dur_ms: int | None = None
        start_ns = self._tool_start_ns.pop((conversation_id, call_id), None)
        if isinstance(start_ns, int):
            dur_ms = int((self._now_ns() - start_ns) / 1_000_000)
        # Cleanup backfilled name mapping after success
        try:
            self._tool_name_by_id.pop((conversation_id, call_id), None)
        except Exception:
            pass
        return ToolStepEvent(
            conversation_id=conversation_id,
            name=final_name,
            args={},
            result_summary=summary,
            status="success",
            duration_ms=dur_ms,
            tool_call_id=call_id,
        )

    @staticmethod
    def _map_tool_invocation(conversation_id: str, name: Any, args: Any) -> ToolStepEvent | None:
        if isinstance(name, str) and name and isinstance(args, dict | list):
            return ToolStepEvent(
                conversation_id=conversation_id,
                name=name,
                args=OpenAIAgentsRunner._normalize_args(args),
                status="start",
                tool_call_id=OpenAIAgentsRunner._gen_tool_call_id(),
            )
        return None

    @staticmethod
    def _map_tool_result(conversation_id: str, name: Any, result: Any) -> ToolStepEvent | None:
        if isinstance(name, str) and name and result is not None:
            return ToolStepEvent(
                conversation_id=conversation_id,
                name=name,
                args={},
                result_summary=OpenAIAgentsRunner._summarize(result),
                status="success",
                tool_call_id=OpenAIAgentsRunner._gen_tool_call_id(),
            )
        return None

    def _map_final_output_event(self, conversation_id: str, item: Any) -> OutputEvent | None:
        if not self._is_final_item(item):
            return None
        text_value = self._extract_text(item)
        if isinstance(text_value, str) and text_value:
            return OutputEvent(
                conversation_id=conversation_id,
                text=text_value,
                usage=self._extract_usage(item),
            )
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
        # Direct attributes
        name = getattr(item, "name", None)
        if isinstance(name, str) and name:
            return name

        # Check nested tool object
        tool_obj = getattr(item, "tool", None)
        if tool_obj is not None:
            tname = getattr(tool_obj, "name", None)
            if isinstance(tname, str) and tname:
                return tname

        # Alternative attributes
        alt = getattr(item, "tool_name", None) or getattr(item, "tool", None)
        if isinstance(alt, str) and alt:
            return alt

        # Raw payload extraction
        return OpenAIAgentsRunner._extract_name_from_raw(item)

    @staticmethod
    def _extract_name_from_raw(item: Any) -> Any:
        # Check raw payload first
        raw = getattr(item, "raw_item", None)
        if raw is not None:
            name = OpenAIAgentsRunner._get_name_from_raw_payload(raw)
            if name is not None:
                return name

        # Fall back to dict-shaped items
        return OpenAIAgentsRunner._get_name_from_dict(item)

    @staticmethod
    def _get_name_from_raw_payload(raw: Any) -> str | None:
        """Extract name from raw payload (Pydantic or dict)."""
        # Pydantic model attributes
        rname = getattr(raw, "name", None)
        if isinstance(rname, str) and rname:
            return rname

        # Dict-like access
        if isinstance(raw, dict):
            return OpenAIAgentsRunner._find_first_valid_string(raw, ("name", "tool_name", "tool"))
        return None

    @staticmethod
    def _get_name_from_dict(item: Any) -> str | None:
        """Extract name from dict-shaped items."""
        if isinstance(item, dict):
            return OpenAIAgentsRunner._find_first_valid_string(item, ("name", "tool_name", "tool"))
        return None

    @staticmethod
    def _find_first_valid_string(data: dict, keys: tuple[str, ...]) -> str | None:
        """Find first valid string value from dict using given keys."""
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _get_args(item: Any) -> Any:
        # Direct attributes
        args = getattr(item, "arguments", None)
        if args is not None:
            return args

        # Check nested tool object
        tool_obj = getattr(item, "tool", None)
        if tool_obj is not None:
            targs = getattr(tool_obj, "arguments", None) or getattr(tool_obj, "input", None)
            if targs is not None:
                return targs

        # Raw payload extraction
        return OpenAIAgentsRunner._extract_args_from_raw(item)

    @staticmethod
    def _extract_args_from_raw(item: Any) -> Any:
        # Raw payload embedded on typed items
        raw = getattr(item, "raw_item", None)
        if raw is not None:
            # Pydantic model attributes (e.g., ResponseFunctionToolCall.arguments)
            rargs = getattr(raw, "arguments", None)
            if rargs is not None:
                return rargs
            if isinstance(raw, dict):
                for key in ("arguments", "args", "input", "parameters"):
                    v = raw.get(key)
                    if v is not None:
                        return v
        # Dict-shaped items
        if isinstance(item, dict):
            return (
                item.get("arguments")
                or item.get("args")
                or item.get("input")
                or item.get("parameters")
            )
        return None

    @staticmethod
    def _get_result(item: Any) -> Any:
        # Direct attributes
        result = getattr(item, "result", None)
        if result is not None:
            return result

        # Alternative attributes
        alt = getattr(item, "output", None) or getattr(item, "content", None)
        if alt is not None:
            return alt

        # Raw payload extraction
        return OpenAIAgentsRunner._extract_result_from_raw(item)

    @staticmethod
    def _extract_result_from_raw(item: Any) -> Any:
        # Check raw payload first
        raw = getattr(item, "raw_item", None)
        if raw is not None:
            result = OpenAIAgentsRunner._get_result_from_raw_payload(raw)
            if result is not None:
                return result

        # Fall back to dict-shaped items
        return OpenAIAgentsRunner._get_result_from_dict(item)

    @staticmethod
    def _get_result_from_raw_payload(raw: Any) -> Any:
        """Extract result from raw payload (Pydantic or dict)."""
        result_keys = ("result", "output_text", "output", "content", "tool_result")

        # Pydantic model attributes
        for attr in result_keys:
            value = getattr(raw, attr, None)
            if value is not None:
                return value

        # Dict-like access
        if isinstance(raw, dict):
            return OpenAIAgentsRunner._find_first_non_none(raw, result_keys)
        return None

    @staticmethod
    def _get_result_from_dict(item: Any) -> Any:
        """Extract result from dict-shaped items."""
        if isinstance(item, dict):
            result_keys = ("result", "output_text", "output", "content", "tool_result")
            return OpenAIAgentsRunner._find_first_non_none(item, result_keys)
        return None

    @staticmethod
    def _find_first_non_none(data: dict, keys: tuple[str, ...]) -> Any:
        """Find first non-None value from dict using given keys."""
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
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
        # No truncation; encode structured results as JSON for stable frontend rendering
        try:
            if isinstance(value, dict | list):
                return json.dumps(value, ensure_ascii=False)
        except Exception:
            pass
        return str(value)

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

    @staticmethod
    def _get_tool_call_id(item: Any | None) -> str | None:
        if item is None:
            return None

        # Check direct attributes in priority order
        for attr in ("id", "tool_call_id", "call_id"):
            val = getattr(item, attr, None)
            if isinstance(val, str) and val:
                return val

        # Raw payload extraction
        return OpenAIAgentsRunner._extract_tool_call_id_from_raw(item)

    @staticmethod
    def _extract_tool_call_id_from_raw(item: Any) -> str | None:
        # Check raw payload first
        raw = getattr(item, "raw_item", None)
        if raw is not None:
            tool_call_id = OpenAIAgentsRunner._get_tool_call_id_from_raw_payload(raw)
            if tool_call_id is not None:
                return tool_call_id

        # Fall back to dict-shaped items
        return OpenAIAgentsRunner._get_tool_call_id_from_dict(item)

    @staticmethod
    def _get_tool_call_id_from_raw_payload(raw: Any) -> str | None:
        """Extract tool call ID from raw payload (Pydantic or dict)."""
        id_keys = ("id", "tool_call_id", "call_id")

        # Pydantic model attributes
        for attr in id_keys:
            value = getattr(raw, attr, None)
            if isinstance(value, str) and value:
                return value

        # Dict-like access
        if isinstance(raw, dict):
            return OpenAIAgentsRunner._find_first_valid_string(raw, id_keys)
        return None

    @staticmethod
    def _get_tool_call_id_from_dict(item: Any) -> str | None:
        """Extract tool call ID from dict-shaped items."""
        if isinstance(item, dict):
            id_keys = ("id", "tool_call_id", "call_id")
            return OpenAIAgentsRunner._find_first_valid_string(item, id_keys)
        return None

    @staticmethod
    def _extract_error(item: Any) -> str | None:
        err = getattr(item, "error", None)
        if isinstance(err, str) and err:
            return err
        if isinstance(item, dict):
            e = item.get("error") or item.get("message")
            if isinstance(e, str) and e:
                return e
        return None

    @staticmethod
    def _gen_tool_call_id() -> str:
        import uuid

        return f"tc_{uuid.uuid4().hex}"

    @staticmethod
    def _now_ns() -> int:
        import time

        return time.perf_counter_ns()


__all__ = ["OpenAIAgentsRunner"]
