from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import Any


def _run(cmd: list[str]) -> int:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        # Print concise stderr on failure to aid debugging without spamming output
        if proc.returncode != 0 and proc.stderr:
            sys.stderr.write(proc.stderr.strip() + "\n")
        return int(proc.returncode)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: failed to run {' '.join(cmd)}: {exc}\n")
        return 1


def ensure_stack() -> int:
    """Ensure docker compose stack is up (redis, gateway, worker).

    Returns 0 on success; non-zero on failure.
    """
    if shutil.which("docker") is None:
        sys.stderr.write("docker not found in PATH. Install Docker to use the stack.\n")
        return 1
    # Bring up services in detached mode; idempotent and quick if already running
    return _run(["docker", "compose", "up", "-d", "redis", "gateway", "worker"])


def _build_passthrough(args: Any, keys: list[str], extra: list[str]) -> list[str]:
    """Build a flat list of CLI passthrough args from a namespace.

    Converts booleans into flags and other values into "--key value" pairs.
    """
    passthrough: list[str] = []
    for key in keys:
        value: Any = getattr(args, key)
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                passthrough.append(flag)
        elif value is not None:
            passthrough.extend([flag, str(value)])
    passthrough.extend(extra)
    return passthrough


def _launch_rust_tui() -> int:
    """Launch the Rust TUI via cargo and return its exit code."""
    try:
        return int(subprocess.call("cd chat_tui && cargo run", shell=True))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: failed to launch Rust TUI: {exc}\n")
        return 1


def launch_client(argv: list[str] | None = None) -> None:
    # Defer import to keep CLI lightweight if used for non-client ops
    from magent2.client.cli import main as client_main

    client_main(argv)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser("magent2")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("ensure", help="Ensure Docker stack (redis, gateway, worker) is up")

    # 'run' ensures stack then launches interactive client for now (TUI later)
    p_run = sub.add_parser(
        "run",
        help="Ensure stack and launch UI (Rust TUI if present; fallback to Python client)",
    )
    # Pass-through common args to client; keep minimal to avoid divergence
    p_run.add_argument("--base-url", default="auto")
    p_run.add_argument("--agent", default="DevAgent")
    p_run.add_argument("--conv")
    p_run.add_argument("--sender")
    p_run.add_argument("--log-level", choices=["debug", "info", "warning", "error"], default="info")
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--max-events", type=int)
    p_run.add_argument("--message")
    p_run.add_argument("--timeout", type=float)

    # 'client' directly launches the client without ensuring stack (advanced users)
    p_client = sub.add_parser("client", help="Launch client without ensuring Docker stack")
    p_client.add_argument("--base-url", default="auto")
    p_client.add_argument("--agent", default="DevAgent")
    p_client.add_argument("--conv")
    p_client.add_argument("--sender")
    p_client.add_argument(
        "--log-level", choices=["debug", "info", "warning", "error"], default="info"
    )
    p_client.add_argument("--quiet", action="store_true")
    p_client.add_argument("--json", action="store_true")
    p_client.add_argument("--max-events", type=int)
    p_client.add_argument("--message")
    p_client.add_argument("--timeout", type=float)

    args, extra = parser.parse_known_args(argv)
    cmd = str(getattr(args, "cmd", "run") or "run")

    if cmd == "ensure":
        code = ensure_stack()
        raise SystemExit(code)

    if cmd == "run":
        code = ensure_stack()
        if code != 0:
            raise SystemExit(code)
        # If Rust TUI exists, launch it; otherwise fall back to Python client
        import os

        tui_manifest = os.path.join(os.getcwd(), "chat_tui", "Cargo.toml")
        if os.path.isfile(tui_manifest):
            raise SystemExit(_launch_rust_tui())
        keys = [
            "base_url",
            "agent",
            "conv",
            "sender",
            "log_level",
            "quiet",
            "json",
            "max_events",
            "message",
            "timeout",
        ]
        launch_client(_build_passthrough(args, keys, extra))
        return

    if cmd == "client":
        keys = [
            "base_url",
            "agent",
            "conv",
            "sender",
            "log_level",
            "quiet",
            "json",
            "max_events",
            "message",
            "timeout",
        ]
        launch_client(_build_passthrough(args, keys, extra))
        return

    # Default to help if unknown
    parser.print_help()


if __name__ == "__main__":
    main()
