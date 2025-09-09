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
        # Optional cutoff: ignore events older than this timestamp (ISO 8601 in data)
        self._since_iso: str | None = None
        # Track AI turn token streaming to avoid duplicating final text
        self._saw_tokens = False
        self._printed_ai_header = False
        # Connection state
        self._connected = threading.Event()
        self._ever_connected = False
        # Event counting for --max-events
        self._events_seen = 0

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

    def _run(self) -> None:
        backoff_delay = 0.5
        while not self._stop.is_set():
            base_url = self._cfg.base_url.rstrip("/")
            url = f"{base_url}/stream/{self._cfg.conversation_id}"
            if self._cfg.max_events is not None and self._cfg.max_events > 0:
                url = f"{url}?max_events={int(self._cfg.max_events)}"
            try:
                timeout = httpx.Timeout(connect=5.0, read=None, write=None, pool=None)
                with httpx.stream("GET", url, timeout=timeout) as resp:
                    if resp.status_code >= 400:
                        if not (self._cfg.quiet or self._cfg.json):
                            self._println(f"[sse] error: {resp.status_code}")
                        # backoff for HTTP errors
                        time.sleep(min(5.0, backoff_delay) + random.uniform(0, 0.2))
                        backoff_delay = min(5.0, backoff_delay * 2)
                        continue
                    # Connected successfully
                    self._connected.set()
                    self._ever_connected = True
                    backoff_delay = 0.5
                    # Basic SSE parsing: lines starting with 'data: '
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            break
                        if not line:
                            continue
                        if line.startswith("data: "):
                            payload = line[len("data: ") :]
                            try:
                                data = json.loads(payload)
                            except Exception:
                                if not (self._cfg.quiet or self._cfg.json):
                                    self._println(f"[event] {payload}")
                                continue
                            self._handle_event(data)
                            self._events_seen += 1
                            if (
                                self._cfg.max_events is not None
                                and self._cfg.max_events > 0
                                and self._events_seen >= int(self._cfg.max_events)
                            ):
                                self._stop.set()
                                break
                    # If we've observed a final output event, stop reconnecting
                    if self._final_event.is_set():
                        break
            except Exception as exc:  # noqa: BLE001
                if not (self._cfg.quiet or self._cfg.json):
                    self._println(f"[sse] reconnecting after error: {exc}")
                time.sleep(min(5.0, backoff_delay) + random.uniform(0, 0.2))
                backoff_delay = min(5.0, backoff_delay * 2)

    def _handle_event(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("event", "")).lower()
        if self._since_iso:
            ev_created = str(data.get("created_at", ""))
            try:
                if ev_created and ev_created < self._since_iso:
                    return
            except Exception:
                return
        # Render modes: json, quiet, pretty (default)
        if getattr(self._cfg, "json", False):
            # Emit one compact JSON object per SSE event
            self._println(json.dumps(data, separators=(",", ":")))
            if event_type == "output":
                text = str(data.get("text", ""))
                self._final_text = text
                self._final_event.set()
            return
        if getattr(self._cfg, "quiet", False):
            if event_type == "output":
                text = str(data.get("text", ""))
                self._println(text)
                self._final_text = text
                self._final_event.set()
            return
        if event_type == "token":
            self._handle_token(data)
            return
        if event_type == "user_message":
            self._handle_user_message(data)
            return
        if event_type == "tool_step":
            self._handle_tool_step(data)
            return
        if event_type == "log":
            self._handle_log(data)
            return
        if event_type == "output":
            self._handle_output(data)
            return
        self._println("")
        self._println(f"[event] {json.dumps(data)[:500]}")

    def _handle_token(self, data: dict[str, Any]) -> None:
        text = str(data.get("text", ""))
        if not self._printed_ai_header:
            self._println("")
            self._print_inline("AI> ")
            self._printed_ai_header = True
        self._print_inline(text)
        self._saw_tokens = True

    def _handle_user_message(self, data: dict[str, Any]) -> None:
        sender = str(data.get("sender", "user"))
        text = str(data.get("text", ""))
        self._println("")
        self._println(f"{sender}> {text}")
        self._saw_tokens = False
        self._printed_ai_header = False

    def _handle_tool_step(self, data: dict[str, Any]) -> None:
        name = data.get("name", "tool")
        summary = data.get("result_summary")
        args = data.get("args")
        summary_text = summary if isinstance(summary, str) else json.dumps(args)[:200]
        self._println("")
        self._println(f"[tool] {name}: {summary_text}")

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
    # Set a cutoff so we ignore stale events; we choose now-100ms to account for clock skew
    cutoff = datetime.now(UTC).timestamp() - 0.1
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
