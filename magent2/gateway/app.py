from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from magent2.bus.interface import Bus, BusMessage
from magent2.bus.utils import compute_publish_topics
from magent2.observability import configure_uvicorn_logging, get_json_logger, get_metrics
from magent2.observability.index import ObserverIndex


# ----------------------------
# SSE utilities
# ----------------------------
def _sse_cap_bytes() -> int | None:
    raw = os.getenv("GATEWAY_SSE_MAX_BYTES", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _truncate_payload_for_sse(payload: dict[str, Any], cap_bytes: int | None) -> dict[str, Any]:
    """Ensure a JSON-serializable payload fits within cap_bytes when encoded.

    If cap is None, return the payload as-is. If payload is too large, attempt
    to truncate the `text` field when present; otherwise emit a minimal
    truncated payload conserving the original event kind.
    """
    if cap_bytes is None:
        return payload

    # Quick fit check
    try:
        s = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(s) <= cap_bytes:
            return payload
    except Exception:
        return _create_minimal_truncated_payload(payload, cap_bytes)

    # Truncate `text` if present
    if isinstance(payload.get("text"), str):
        truncated = _truncate_text_field(payload, cap_bytes)
        if truncated:
            return truncated

    # Fallback to minimal payload
    return _create_minimal_truncated_payload(payload, cap_bytes)


def _truncate_text_field(payload: dict[str, Any], cap_bytes: int) -> dict[str, Any] | None:
    """Try to truncate the `text` field so the JSON fits within cap_bytes."""
    try:
        result = dict(payload)
        original_text = result["text"]

        # Compute overhead without text content
        base = dict(result)
        base["text"] = ""
        base["truncated"] = True
        base["cap_bytes"] = cap_bytes

        overhead = len(json.dumps(base, separators=(",", ":")).encode("utf-8"))
        allowed_text_bytes = max(0, cap_bytes - overhead)

        text_bytes = original_text.encode("utf-8")
        trimmed = text_bytes[:allowed_text_bytes].decode("utf-8", errors="ignore")
        base["text"] = trimmed

        if len(json.dumps(base, separators=(",", ":")).encode("utf-8")) <= cap_bytes:
            return base
    except Exception:
        pass
    return None


def _create_minimal_truncated_payload(payload: dict[str, Any], cap_bytes: int) -> dict[str, Any]:
    """Create a compact truncated payload that fits within cap_bytes."""
    try:
        # Safe way to get event type
        event_type = "output"
        if isinstance(payload, dict) and "event" in payload:
            event_type = str(payload["event"])

        minimal = {
            "event": event_type,
            "truncated": True,
            "cap_bytes": cap_bytes,
        }

        # Verify it fits
        minimal_json = json.dumps(minimal, separators=(",", ":")).encode("utf-8")
        if len(minimal_json) <= cap_bytes:
            return minimal
    except Exception:
        pass
    return {"event": "truncated"}


class SendRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    sender: str
    recipient: str
    type: Literal["message"] = "message"
    content: str


def create_app(bus: Bus) -> FastAPI:
    app = FastAPI()
    # Configure uvicorn logging at app startup to avoid import-time side effects
    configure_uvicorn_logging()
    logger = get_json_logger("magent2.gateway")
    metrics = get_metrics()
    # Shutdown signal used to encourage prompt exit of long‑lived generators
    shutdown_flag: asyncio.Event = asyncio.Event()

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        try:
            shutdown_flag.set()
        except Exception:
            pass
        logger.info(
            "gateway shutdown",
            extra={"event": "gateway_shutdown", "service": "gateway"},
        )

    obs_index = ObserverIndex.from_bus(bus)

    @app.get("/health")
    async def health() -> dict[str, str]:  # lightweight healthcheck endpoint
        return {"status": "ok"}

    @app.post("/send")
    async def send(message: SendRequest) -> dict[str, Any]:
        payload = message.model_dump(mode="json")
        conv_topic = f"chat:{message.conversation_id}"

        def _publish_or_503(
            topic: str, *, stage: str | None = None, extra: dict[str, Any] | None = None
        ) -> None:
            try:
                bus.publish(topic, BusMessage(topic=topic, payload=payload))
            except Exception as exc:  # pragma: no cover - error path mapping
                fields: dict[str, Any] = {
                    "event": "gateway_error",
                    "path": "send",
                    "conversation_id": message.conversation_id,
                }
                if stage:
                    fields["stage"] = stage
                if isinstance(extra, dict):
                    fields.update(extra)
                logger.error("gateway send error", extra=fields)
                metrics.increment(
                    "gateway_bus_publish_errors",
                    {"path": "send", "conversation_id": message.conversation_id, **(extra or {})},
                )
                raise HTTPException(status_code=503, detail="bus publish failed") from exc

        # Publish to conversation and optional agent topics
        for topic in compute_publish_topics(message.recipient, message.conversation_id):
            extra: dict[str, Any] | None = None
            if topic != conv_topic and topic.startswith("chat:"):
                extra = {"agent": topic.split(":", 1)[1]}
            _publish_or_503(topic, extra=extra)

        # Publish a stream-visible user_message event so clients can render inbound messages
        stream_topic = f"stream:{message.conversation_id}"
        user_event = {
            "event": "user_message",
            "conversation_id": message.conversation_id,
            "sender": message.sender,
            "text": message.content,
            # RFC3339 timestamp for client-side staleness filtering
            "created_at": datetime.now(UTC).isoformat(),
        }
        stream_payload = BusMessage(topic=stream_topic, payload=user_event).payload
        try:
            bus.publish(stream_topic, BusMessage(topic=stream_topic, payload=stream_payload))
        except Exception as exc:  # pragma: no cover - error path mapping
            logger.error(
                "gateway send error",
                extra={
                    "event": "gateway_error",
                    "path": "send",
                    "conversation_id": message.conversation_id,
                    "stage": "stream_user_message",
                },
            )
            metrics.increment(
                "gateway_bus_publish_errors",
                {
                    "path": "send",
                    "conversation_id": message.conversation_id,
                    "stage": "stream_user_message",
                },
            )
            raise HTTPException(status_code=503, detail="bus publish failed") from exc

        logger.info(
            "gateway send",
            extra={
                "event": "gateway_send",
                "service": "gateway",
                "conversation_id": message.conversation_id,
                "attributes": {
                    "sender": message.sender,
                    "recipient": message.recipient,
                    "content_len": len(message.content or ""),
                },
            },
        )
        metrics.increment("gateway_sends", {"conversation_id": message.conversation_id})
        # Best-effort: write to observer index (no-op if disabled/unavailable)
        try:
            obs_index.record_user_message(
                message.conversation_id, message.sender, message.recipient, message.content, None
            )
        except Exception:
            pass
        return {"status": "ok", "topic": conv_topic}

    @app.get("/stream/{conversation_id}")
    async def stream(
        conversation_id: str,
        request: Request,
        max_events: int | None = None,
        last_id: str | None = None,
    ) -> Response:
        """Server‑Sent Events stream for a conversation.

        Semantics:
        - All `token` events are forwarded as they are produced, enabling
          real‑time incremental rendering in clients.
        - `output` and `tool_step` events are forwarded as‑is.

        Parameters:
        - conversation_id: stream topic key (`stream:{conversation_id}`)
        - max_events: optional testing aid to stop after N events
        """
        topic = f"stream:{conversation_id}"

        async def event_gen() -> Any:
            cursor: str | None = last_id or request.headers.get("Last-Event-ID") or None
            sent = 0
            cap = _sse_cap_bytes()
            # Detect if the underlying bus supports blocking reads (e.g., Redis consumer groups)
            try:
                blocking_supported = (
                    bool(getattr(bus, "_group", None))
                    and int(getattr(bus, "_block_ms", 0) or 0) > 0
                )
            except Exception:
                blocking_supported = False
            # Simple polling loop over Bus.read
            import time as _time

            last_hb = _time.monotonic()
            try:
                while True:
                    # Exit promptly if the app is shutting down
                    if shutdown_flag.is_set():
                        return
                    # Exit promptly if client disconnects (including server shutdown)
                    try:
                        if await request.is_disconnected():
                            return
                    except Exception:
                        # Best-effort; continue if disconnect check fails
                        pass

                    items = await asyncio.to_thread(
                        lambda: list(bus.read(topic, last_id=cursor, limit=100))
                    )
                    if items:
                        for m in items:
                            payload = m.payload
                            safe_payload = _truncate_payload_for_sse(payload, cap)
                            data = json.dumps(safe_payload, separators=(",", ":"))
                            # Emit SSE id for resume support
                            yield f"id: {m.id}\n"
                            yield f"data: {data}\n\n"
                            cursor = m.id
                            sent += 1
                            if max_events is not None and sent >= max_events:
                                return
                    else:
                        # avoid tight loop when no new items are available
                        if not blocking_supported:
                            await asyncio.sleep(0.02)
                    # Heartbeat every 15s to keep connections alive through proxies
                    now = _time.monotonic()
                    if now - last_hb >= 15.0:
                        last_hb = now
                        yield ":\n\n"
            except asyncio.CancelledError:
                # Gracefully exit on task cancellation during server shutdown
                return

        logger.info(
            "gateway stream start",
            extra={
                "event": "gateway_stream",
                "service": "gateway",
                "conversation_id": conversation_id,
                "last_event_id": request.headers.get("Last-Event-ID") or last_id or "",
            },
        )
        metrics.increment("gateway_streams", {"conversation_id": conversation_id})
        sse_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Hint for reverse proxies like Nginx to disable response buffering
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(event_gen(), media_type="text/event-stream", headers=sse_headers)

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        try:
            # Perform a harmless read on a probe topic to validate connectivity
            list(bus.read("ready:probe", last_id=None, limit=1))
            return {"status": "ok"}
        except Exception as exc:  # pragma: no cover - error path mapping
            logger.error(
                "gateway not ready",
                extra={"event": "gateway_error", "service": "gateway", "path": "ready"},
            )
            metrics.increment("gateway_ready_errors", {})
            raise HTTPException(status_code=503, detail="bus not ready") from exc

    # ----------------------------
    # Observer endpoints (read-only)
    # ----------------------------

    @app.get("/conversations")
    async def conversations(limit: int = 50, since_ms: int | None = None) -> dict[str, Any]:
        try:
            n = max(1, min(200, int(limit)))
        except Exception:
            n = 50
        try:
            items = obs_index.list_conversations(limit=n, since_ms=since_ms)
        except Exception:
            items = []
        return {"conversations": items}

    @app.get("/agents")
    async def agents() -> dict[str, Any]:
        try:
            items = obs_index.list_agents()
        except Exception:
            items = []
        return {"agents": items}

    @app.get("/graph/{conversation_id}")
    async def graph(conversation_id: str) -> dict[str, Any]:
        # If index is disabled or unavailable, return empty graph gracefully
        try:
            if not obs_index.is_active():
                return {"nodes": [], "edges": []}
            if not obs_index.conversation_exists(conversation_id):
                raise HTTPException(status_code=404, detail="unknown conversation_id")
            g = obs_index.get_graph(conversation_id) or {"nodes": [], "edges": []}
            return g
        except HTTPException:
            raise
        except Exception:
            return {"nodes": [], "edges": []}

    return app
