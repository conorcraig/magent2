from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ClientConfig:
    base_url: str
    conversation_id: str
    agent_name: str
    sender: str


class StreamPrinter:
    """Background SSE stream reader that pretty-prints events.

    Keeps reconnecting on transient errors. Call stop() to terminate.
    """

    def __init__(self, cfg: ClientConfig) -> None:
        self._cfg = cfg
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._print_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
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

    def _run(self) -> None:
        while not self._stop.is_set():
            url = f"{self._cfg.base_url.rstrip('/')}/stream/{self._cfg.conversation_id}"
            try:
                with httpx.stream("GET", url, timeout=None) as resp:
                    if resp.status_code >= 400:
                        self._println(f"[sse] error: {resp.status_code}")
                        time.sleep(0.5)
                        continue
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
                                self._println(f"[event] {payload}")
                                continue
                            self._handle_event(data)
            except Exception as exc:  # noqa: BLE001
                self._println(f"[sse] reconnecting after error: {exc}")
                time.sleep(0.5)

    def _handle_event(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("event", "")).lower()
        if event_type == "token":
            text = str(data.get("text", ""))
            # Print tokens inline
            self._print_inline(text)
            return
        if event_type == "tool_step":
            name = data.get("name", "tool")
            summary = data.get("result_summary")
            args = data.get("args")
            summary_text = summary if isinstance(summary, str) else json.dumps(args)[:200]
            self._println("")
            self._println(f"[tool] {name}: {summary_text}")
            return
        if event_type == "output":
            text = str(data.get("text", ""))
            # Ensure newline then print final text line
            self._println("")
            self._println(f"AI> {text}")
            return
        # Fallback
        self._println("")
        self._println(f"[event] {json.dumps(data)[:500]}")


def _send_message(cfg: ClientConfig, content: str) -> None:
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
            print(f"[send] error: {r.status_code} {r.text}")
    except Exception as exc:  # noqa: BLE001
        print(f"[send] request failed: {exc}")


def _default_sender() -> str:
    user = os.getenv("USER") or os.getenv("USERNAME") or "user"
    return f"user:{user}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser("magent2 client")
    p.add_argument("--base-url", default=os.getenv("MAGENT2_BASE_URL", "http://localhost:8000"))
    p.add_argument("--conv", default=f"conv-{str(uuid.uuid4())[:8]}")
    p.add_argument("--agent", default=os.getenv("AGENT_NAME", "DevAgent"))
    p.add_argument("--sender", default=_default_sender())
    return p.parse_args(argv)


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


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = ClientConfig(
        base_url=str(args.base_url),
        conversation_id=str(args.conv),
        agent_name=str(args.agent),
        sender=str(args.sender),
    )
    repl(cfg)


if __name__ == "__main__":
    main()
