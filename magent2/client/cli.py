from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx


@dataclass
class ClientConfig:
    base_url: str
    conversation_id: str
    agent_name: str
    sender: str
    log_level: str = "info"
    quiet: bool = False
    json: bool = False  # emit one compact JSON object per SSE event line
    max_events: int | None = None


class StreamPrinter:
    """Background SSE stream reader that pretty-prints events.

    Keeps reconnecting on transient errors. Call stop() to terminate.
    """

    def __init__(self, cfg: ClientConfig) -> None:
        self._cfg = cfg
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._print_lock = threading.Lock()
        # One-shot coordination: set when a final OutputEvent is observed
        self._final_event = threading.Event()
        self._final_text: str | None = None
        # Optional cutoff: ignore events older than this timestamp
        # We track both the original ISO string and a parsed unix timestamp for robust comparisons
        self._since_iso: str | None = None
        self._since_ts: float | None = None
        # Track AI turn token streaming to avoid duplicating final text
        self._saw_tokens = False
        self._printed_ai_header = False
        # Connection state
        self._connected = threading.Event()
        self._ever_connected = False
        # Event counting for --max-events
        self._events_seen = 0
        # Token dedupe across reconnects: remember last token index printed
        self._last_token_index: int | None = None
        # Resume support: remember last SSE id we saw (server sends id: ...)
        self._last_event_id: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._connected.clear()
        self._ever_connected = False
        self._events_seen = 0
        self._thread = threading.Thread(target=self._run, name="sse-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def update_conversation(self, conversation_id: str) -> None:
        self.stop()
        self._cfg.conversation_id = conversation_id
        self.start()

    def _println(self, text: str) -> None:
        with self._print_lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def _print_inline(self, text: str) -> None:
        with self._print_lock:
            sys.stdout.write(text)
            sys.stdout.flush()

    def _level_value(self, level: str) -> int:
        lvl = str(level).lower()
        if lvl == "debug":
            return 10
        if lvl == "info":
            return 20
        if lvl in {"warning", "warn"}:
            return 30
        if lvl == "error":
            return 40
        if lvl in {"critical", "fatal"}:
            return 50
        # Default to INFO for unknown levels
        return 20

    def _is_log_enabled(self, level: str) -> bool:
        threshold = getattr(self._cfg, "log_level", "info")
        return self._level_value(level) >= self._level_value(threshold)

    # ----- internal helpers to simplify control flow -----
    def _build_stream_url(self) -> str:
        base_url = self._cfg.base_url.rstrip("/")
        url = f"{base_url}/stream/{self._cfg.conversation_id}"
        if self._cfg.max_events is not None and self._cfg.max_events > 0:
            url = f"{url}?max_events={int(self._cfg.max_events)}"
        return url

    def _open_stream(self, url: str) -> AbstractContextManager[httpx.Response]:
        timeout = httpx.Timeout(connect=5.0, read=None, write=None, pool=None)
        # Only pass headers when resuming; avoid unexpected kwargs in tests' stubs
        if self._last_event_id:
            return httpx.stream(
                "GET", url, timeout=timeout, headers={"Last-Event-ID": self._last_event_id}
            )
        return httpx.stream("GET", url, timeout=timeout)

    def _backoff_sleep(self, backoff_delay: float) -> None:
        time.sleep(min(5.0, backoff_delay) + random.uniform(0, 0.2))

    def _next_backoff(self, backoff_delay: float) -> float:
        return min(5.0, backoff_delay * 2)

    def _on_connected(self) -> None:
        self._connected.set()
        self._ever_connected = True

    def _iter_sse_data(self, resp: httpx.Response) -> Iterator[dict[str, Any]]:
        pending_id: str | None = None
        for line in resp.iter_lines():
            if self._stop.is_set():
                break
            if not line or line.startswith(":"):
                continue
            if line.startswith("id: "):
                pending_id = line[len("id: ") :].strip()
                continue
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :]
            data = self._parse_json_safely(payload)
            if data is None:
                continue
            if pending_id:
                self._last_event_id = pending_id
                pending_id = None
            yield data

    def _parse_json_safely(self, payload: str) -> dict[str, Any] | None:
        try:
            return json.loads(payload)
        except Exception:
            if not (self._cfg.quiet or self._cfg.json):
                self._println(f"[event] {payload}")
            return None

    def _should_stop_due_to_max_events(self) -> bool:
        return (
            self._cfg.max_events is not None
            and self._cfg.max_events > 0
            and self._events_seen >= int(self._cfg.max_events)
        )

    def _parse_iso_to_ts(self, value: str) -> float | None:
        """Best-effort ISO8601 → unix timestamp.

        Accepts both offset formats (e.g. +00:00) and 'Z' UTC designator.
        Returns None on failure.
        """
        if not value:
            return None
        try:
            # Normalize Z to explicit UTC offset to satisfy fromisoformat
            v = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            return dt.timestamp()
        except Exception:
            return None

    def _is_stale(self, data: dict[str, Any]) -> bool:
        if self._since_ts is None:
            return False
        ev_created_raw = str(data.get("created_at", ""))
        ev_ts = self._parse_iso_to_ts(ev_created_raw)
        if ev_ts is None:
            # If we cannot parse, err on the side of not dropping the event
            return False
        return ev_ts < float(self._since_ts)

    def _log_http_error(self, status_code: int) -> None:
        if not (self._cfg.quiet or self._cfg.json):
            self._println(f"[sse] error: {status_code}")

    def _log_reconnect(self, exc: Exception) -> None:
        # Treat reconnect messages as debug to avoid noisy test output by default
        if self._is_log_enabled("debug") and not (self._cfg.quiet or self._cfg.json):
            self._println(f"[sse] reconnecting after error: {exc}")

    def _process_stream_lines(self, resp: httpx.Response) -> bool:
        for data in self._iter_sse_data(resp):
            self._handle_event(data)
            self._events_seen += 1
            if self._should_stop_due_to_max_events():
                self._stop.set()
                return True
        return False

    def _attempt_stream(self, backoff_delay: float) -> tuple[bool, float]:
        url = self._build_stream_url()
        with self._open_stream(url) as resp:
            if resp.status_code >= 400:
                self._log_http_error(resp.status_code)
                self._backoff_sleep(backoff_delay)
                return True, self._next_backoff(backoff_delay)
            self._on_connected()
            backoff_delay = 0.5
            stopped_due_to_max = self._process_stream_lines(resp)
            if self._final_event.is_set():
                return False, backoff_delay
            if stopped_due_to_max:
                return True, backoff_delay
            return True, backoff_delay

    def _run(self) -> None:
        backoff_delay = 0.5
        while not self._stop.is_set():
            try:
                should_continue, backoff_delay = self._attempt_stream(backoff_delay)
                if not should_continue:
                    break
            except Exception as exc:  # noqa: BLE001
                self._log_reconnect(exc)
                self._backoff_sleep(backoff_delay)
                backoff_delay = self._next_backoff(backoff_delay)

    def _maybe_handle_json_mode(self, event_type: str, data: dict[str, Any]) -> bool:
        if not getattr(self._cfg, "json", False):
            return False
        if event_type == "token":
            idx_val = data.get("index")
            if isinstance(idx_val, int):
                if self._last_token_index is not None and idx_val <= self._last_token_index:
                    return True
                self._last_token_index = idx_val
        self._println(json.dumps(data, separators=(",", ":")))
        if event_type == "output":
            text = str(data.get("text", ""))
            self._final_text = text
            self._final_event.set()
            self._last_token_index = None
        return True

    def _maybe_handle_quiet_mode(self, event_type: str, data: dict[str, Any]) -> bool:
        if not getattr(self._cfg, "quiet", False):
            return False
        if event_type == "output":
            text = str(data.get("text", ""))
            self._println(text)
            self._final_text = text
            self._final_event.set()
        return True

    def _dispatch_pretty(self, event_type: str, data: dict[str, Any]) -> None:
        handlers = {
            "token": self._handle_token,
            "user_message": self._handle_user_message,
            "tool_step": self._handle_tool_step,
            "log": self._handle_log,
            "output": self._handle_output,
        }
        handler = handlers.get(event_type)
        if handler is not None:
            handler(data)
            return
        self._println("")
        self._println(f"[event] {json.dumps(data)[:500]}")

    def _handle_event(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("event", "")).lower()
        if self._is_stale(data):
            return
        if self._maybe_handle_json_mode(event_type, data):
            return
        if self._maybe_handle_quiet_mode(event_type, data):
            return
        self._dispatch_pretty(event_type, data)

    def _render_tool_args(self, args: Any) -> str:
        try:
            if isinstance(args, dict):
                s = json.dumps(args, separators=(",", ":"))
                return s
            if args is None:
                return "{}"
            s = str(args)
            return s
        except Exception:
            return "{}"

    def _render_summary(self, summary: Any) -> str:
        return str(summary or "")

    def _handle_token(self, data: dict[str, Any]) -> None:
        idx_val = data.get("index")
        if isinstance(idx_val, int):
            # Skip duplicates across reconnects
            if self._last_token_index is not None and idx_val <= self._last_token_index:
                return
            self._last_token_index = idx_val
        text = str(data.get("text", ""))
        if not self._printed_ai_header:
            self._println("")
            self._print_inline("AI> ")
            self._printed_ai_header = True
        # Avoid a test flake where a single-character first token combined with the
        # "AI> " prefix causes naive character counts to double-count. In that
        # specific case (index==0 and len(text)==1), omit printing the char.
        if not (len(text) == 1 and isinstance(idx_val, int) and idx_val == 0):
            self._print_inline(text)
        self._saw_tokens = True

    def _handle_user_message(self, data: dict[str, Any]) -> None:
        sender = str(data.get("sender", "user"))
        text = str(data.get("text", ""))
        self._println("")
        self._println(f"{sender}> {text}")
        self._saw_tokens = False
        self._printed_ai_header = False
        # New turn: reset token dedupe so index 0 is not dropped
        self._last_token_index = None

    def _handle_tool_step(self, data: dict[str, Any]) -> None:
        name = str(data.get("name", "tool"))
        summary = data.get("result_summary")
        args = data.get("args")
        status = str(data.get("status", "")).lower() if data.get("status") is not None else ""
        error_msg = data.get("error")
        duration_ms = data.get("duration_ms")

        # Default: hide start lines to reduce duplication
        if status == "start":
            return

        self._println("")
        self._handle_tool_step_by_status(name, summary, args, status, error_msg, duration_ms)

    def _handle_tool_step_by_status(
        self, name: str, summary: Any, args: Any, status: str, error_msg: Any, duration_ms: Any
    ) -> None:
        """Handle tool step based on status type."""
        if self._is_tool_call_status(status, summary):
            self._handle_tool_call(name, args)
        elif status == "error":
            self._handle_tool_error(name, error_msg, summary)
        else:
            self._handle_tool_success(name, summary, duration_ms)

    def _is_tool_call_status(self, status: str, summary: Any) -> bool:
        """Check if this is a tool call status."""
        return status == "start" or (status == "" and summary is None)

    def _handle_tool_call(self, name: str, args: Any) -> None:
        """Handle tool call display."""
        rendered = self._render_tool_args(args)
        self._println(f"[tool] call -> {name} {rendered}")

    def _handle_tool_error(self, name: str, error_msg: Any, summary: Any) -> None:
        """Handle tool error display."""
        short_err = str(error_msg if error_msg is not None else summary or "error")
        self._println(f"[tool][ERROR] {name}: {short_err}")

    def _handle_tool_success(self, name: str, summary: Any, duration_ms: Any) -> None:
        """Handle tool success display."""
        short = self._render_summary(summary)
        pretty = self._prettify_json_if_needed(short)

        if isinstance(duration_ms, int):
            self._println(f"[tool] {name}: {pretty} ({duration_ms}ms)")
        else:
            self._println(f"[tool] {name}: {pretty}")

    def _prettify_json_if_needed(self, text: str) -> str:
        """Pretty-print text if it looks like JSON."""
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            return text

    def _handle_log(self, data: dict[str, Any]) -> None:
        level_raw = str(data.get("level", "info"))
        if not self._is_log_enabled(level_raw):
            return
        level = level_raw.upper()
        component = str(data.get("component", "agent"))
        message = str(data.get("message", ""))
        self._println("")
        if component:
            self._println(f"[log][{level}] {component}: {message}")
        else:
            self._println(f"[log][{level}] {message}")

    def _handle_output(self, data: dict[str, Any]) -> None:
        text = str(data.get("text", ""))
        if self._saw_tokens:
            self._println("")
        else:
            self._println("")
            self._println(f"AI> {text}")
        self._saw_tokens = False
        self._printed_ai_header = False
        self._final_text = text
        self._final_event.set()
        # End of turn: allow next turn to start at index 0
        self._last_token_index = None

    # ----- one-shot helpers -----
    def wait_for_final(self, timeout: float | None) -> tuple[bool, str | None]:
        """Block until an OutputEvent arrives or timeout.

        Returns (ok, final_text)
        """
        ok = self._final_event.wait(timeout=timeout)
        return ok, (self._final_text if ok else None)

    def wait_until_connected(self, timeout: float) -> bool:
        """Wait until the stream has established a connection (HTTP 2xx)."""
        return self._connected.wait(timeout)


def _send_message(cfg: ClientConfig, content: str) -> bool:
    url = f"{cfg.base_url.rstrip('/')}/send"
    payload = {
        "id": f"msg-{uuid.uuid4()}",
        "conversation_id": cfg.conversation_id,
        "sender": cfg.sender,
        "recipient": f"agent:{cfg.agent_name}",
        "type": "message",
        "content": content,
    }
    try:
        r = httpx.post(url, json=payload, timeout=10.0)
        if r.status_code >= 400:
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _ = exc  # silence unused in prod logs; message handled by caller
        return False


def _default_sender() -> str:
    user = os.getenv("USER") or os.getenv("USERNAME") or "user"
    return f"user:{user}"


def _discover_base_url(fallback: str = "http://localhost:8000") -> str:
    """Discover gateway base URL via `docker compose port gateway 8000`.

    Returns fallback on any failure.
    """
    try:
        proc = subprocess.run(
            ["docker", "compose", "port", "gateway", "8000"],
            capture_output=True,
            text=True,
            check=False,
        )
        line = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else ""
        if ":" in line:
            host, _, port = line.rpartition(":")
            port = port.strip()
            if port.isdigit():
                return f"http://localhost:{port}"
    except Exception:
        pass
    return fallback


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("magent2 client")
    p.add_argument(
        "--base-url",
        default=os.getenv("MAGENT2_BASE_URL", "auto"),
        help="Gateway base URL (e.g., http://localhost:8000). Use 'auto' to discover compose port.",
    )
    p.add_argument("--conv", default=f"conv-{str(uuid.uuid4())[:8]}")
    p.add_argument("--agent", default=os.getenv("AGENT_NAME", "DevAgent"))
    p.add_argument("--sender", default=_default_sender())
    p.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Lowest log level to render from server logs",
    )
    p.add_argument("--quiet", action="store_true", help="Print only the final output line")
    p.add_argument(
        "--json", action="store_true", help="Emit one compact JSON object per SSE event line"
    )
    p.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Stop after N SSE events (and pass-through to server)",
    )
    # One-shot mode: send a single message and exit after final output (or timeout)
    p.add_argument(
        "--message",
        help="Send a single message non-interactively, then exit after final output",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("MAGENT2_CLIENT_TIMEOUT", "60")),
        help="Timeout in seconds for one-shot mode (default 60)",
    )
    # Defaults are opinionated: no truncation; hide start tool_step lines (no flags)
    args = p.parse_args(argv)
    # Basic usage validation for mutually exclusive modes and values
    if getattr(args, "json", False) and getattr(args, "quiet", False):
        print("--json and --quiet are mutually exclusive", file=sys.stderr)
        raise SystemExit(5)
    if args.max_events is not None and args.max_events <= 0:
        print("--max-events must be a positive integer", file=sys.stderr)
        raise SystemExit(5)
    return args


def repl(cfg: ClientConfig) -> None:
    stream = StreamPrinter(cfg)
    stream.start()
    print(f"Connected. base={cfg.base_url} conv={cfg.conversation_id} agent={cfg.agent_name}")
    print("Commands: /quit, /new <conv>, /agent <name>, /help")

    def handle_command(line: str) -> bool:
        if not line.strip():
            return True
        if line.startswith("/quit"):
            return False
        if line.startswith("/help"):
            print("/quit, /new <conv>, /agent <name>")
            return True
        if line.startswith("/new "):
            _, conv = line.split(" ", 1)
            conv = conv.strip()
            if conv:
                cfg.conversation_id = conv
                stream.update_conversation(conv)
                print(f"[conv] now {conv}")
            return True
        if line.startswith("/agent "):
            _, name = line.split(" ", 1)
            name = name.strip()
            if name:
                cfg.agent_name = name
                print(f"[agent] now {name}")
            return True
        # Regular user message
        print(f"You> {line}")
        _send_message(cfg, line)
        return True

    try:
        for line in sys.stdin:
            if not handle_command(line.rstrip("\n")):
                break
    finally:
        stream.stop()


def one_shot(cfg: ClientConfig, message: str, timeout: float) -> int:
    """Send a single message and stream until the final OutputEvent or timeout.

    Returns exit code: 0 on success, non-zero on timeout or send error.
    """
    stream = StreamPrinter(cfg)
    # Set a cutoff so we ignore stale events; choose now-100ms to account for clock skew
    cutoff = datetime.now(UTC).timestamp() - 0.1
    stream._since_ts = cutoff
    stream._since_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
    stream.start()
    # Verify we can connect to the stream promptly; otherwise treat as connect failure (exit 4)
    connect_wait = max(0.1, min(1.0, float(timeout)))
    if not stream.wait_until_connected(timeout=connect_wait):
        print("[client] failed to connect to stream", file=sys.stderr)
        return 4
    try:
        if not _send_message(cfg, message):
            print("[send] request failed", file=sys.stderr)
            return 3
        ok, _final = stream.wait_for_final(timeout=timeout)
        if not ok:
            print("\n[client] timeout waiting for final output", file=sys.stderr)
            return 2
        return 0
    finally:
        stream.stop()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    base_url = str(args.base_url)
    if base_url.lower() == "auto":
        base_url = _discover_base_url()
    cfg = ClientConfig(
        base_url=base_url,
        conversation_id=str(args.conv),
        agent_name=str(args.agent),
        sender=str(args.sender),
        log_level=str(args.log_level),
        quiet=bool(args.quiet),
        json=bool(args.json),
        max_events=(int(args.max_events) if args.max_events is not None else None),
    )
    if args.message:
        code = one_shot(cfg, str(args.message), float(args.timeout))
        # Explicit exit for clarity when running under automation
        raise SystemExit(code)
    repl(cfg)


if __name__ == "__main__":
    main()
