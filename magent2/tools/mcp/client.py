from __future__ import annotations

import json
import os
import queue
import sys as _sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from subprocess import PIPE, Popen
from types import TracebackType
from typing import IO, Any


def _now_ms() -> int:
    return int(time.time() * 1000)


class FramingError(RuntimeError):
    pass


def _read_frame(stdout: IO[bytes]) -> dict[str, Any]:
    """Read one JSON-RPC message framed with MCP stdio headers.

    Format:
    Content-Length: <n>\r\n
    <n bytes of JSON>
    """
    # Read first header line
    header_line: bytes = stdout.readline()
    if not header_line:
        # EOF
        raise EOFError("EOF while reading header line")
    try:
        header_decoded = header_line.decode().strip()
    except UnicodeDecodeError as exc:  # pragma: no cover - defensive
        raise FramingError("Failed to decode header line") from exc
    if not header_decoded.lower().startswith("content-length:"):
        raise FramingError("Missing Content-Length header")
    try:
        length = int(header_decoded.split(":", 1)[1].strip())
    except ValueError as exc:  # pragma: no cover - defensive
        raise FramingError("Invalid Content-Length header") from exc

    # Expect empty line after headers
    blank: bytes = stdout.readline()
    if not blank:
        raise FramingError("Missing CRLF after headers")

    body = stdout.read(length)
    if body is None or len(body) != length:
        raise FramingError("Truncated body")
    try:
        return json.loads(body.decode())
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise FramingError("Invalid JSON body") from exc


def _write_frame(stdin: IO[bytes], payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode()
    stdin.write(f"Content-Length: {len(data)}\r\n".encode())
    stdin.write(b"\r\n")
    stdin.write(data)
    stdin.flush()


@dataclass
class _Pending:
    created_ms: int
    response_queue: queue.Queue[dict[str, Any]]


class MCPClient:
    """Minimal MCP stdio JSON-RPC client.

    Synchronous, single-threaded request API with a background reader thread.
    """

    def __init__(self, proc: Popen[bytes]) -> None:
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("Process must have stdin/stdout pipes")
        self._proc = proc
        self._stdin = proc.stdin
        self._stdout = proc.stdout
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="mcp-reader",
            daemon=True,
        )
        self._alive = True
        self._reader.start()

    # Public API

    def initialize(self, timeout: float = 5.0) -> dict[str, Any]:
        return self._request("initialize", {}, timeout)

    def list_tools(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        result = self._request("tools/list", {}, timeout)
        tools = result.get("tools")
        if not isinstance(tools, list):  # pragma: no cover - defensive
            raise RuntimeError("Invalid tools/list result")
        return tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments or {}}
        return self._request("tools/call", params, timeout)

    def shutdown(self, timeout: float = 3.0) -> dict[str, Any]:
        try:
            return self._request("shutdown", {}, timeout)
        except Exception:
            return {"ok": False}

    def close(self) -> None:
        self._alive = False
        try:
            self.shutdown()
        except Exception:
            pass
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    # Context manager
    def __enter__(self) -> MCPClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self.close()
        # Propagate exceptions by returning None (do not suppress)
        return None

    # Internals
    def _reader_loop(self) -> None:
        while self._alive:
            try:
                message = _read_frame(self._stdout)
            except EOFError:
                break
            except Exception:
                # Any error: stop reader gracefully
                break
            # Handle JSON-RPC response
            msg_id = message.get("id")
            if isinstance(msg_id, int):
                pending = self._pending.get(msg_id)
                if pending is not None:
                    pending.response_queue.put(message)

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            pending = _Pending(created_ms=_now_ms(), response_queue=queue.Queue())
            self._pending[msg_id] = pending
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": method,
                "params": params,
            }
            _write_frame(self._stdin, payload)

        try:
            response = pending.response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            # Cleanup pending to avoid leaks
            with self._lock:
                self._pending.pop(msg_id, None)
            raise TimeoutError(f"Timed out waiting for response to {method}") from exc

        with self._lock:
            self._pending.pop(msg_id, None)

        if "error" in response:
            raise RuntimeError(f"RPC error: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):  # pragma: no cover - defensive
            raise RuntimeError("Invalid RPC response: missing result")
        return result


@contextmanager
def spawn_stdio_server(
    cmd: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[MCPClient]:
    """Spawn an MCP stdio server process and yield an MCPClient.

    The process is terminated on context exit.
    """
    # Use a sanitized env if provided, otherwise inherit current env
    proc = Popen(
        cmd,
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        cwd=cwd,
        env=env,
        bufsize=0,
    )
    # Optional debug: mirror child stderr to our stderr when enabled
    if os.getenv("MCP_DEBUG_STDERR") in {"1", "true", "True"} and proc.stderr is not None:
        # Narrow type for mypy by capturing into a local variable
        stderr = proc.stderr

        def _drain_stderr() -> None:
            try:
                for line in iter(stderr.readline, b""):
                    try:
                        _sys.stderr.write(line.decode(errors="replace"))
                    except Exception:
                        pass
            except Exception:
                pass

        t = threading.Thread(target=_drain_stderr, name="mcp-stderr", daemon=True)
        t.start()
    client = MCPClient(proc)
    try:
        # Caller is responsible for sending initialize(), to allow flexible handshakes
        yield client
    finally:
        client.close()


__all__ = ["MCPClient", "spawn_stdio_server"]
