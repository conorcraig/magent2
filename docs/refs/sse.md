# Server‑Sent Events (SSE) (Gateway streaming)

- Protocol: HTTP response with `Content-Type: text/event-stream`. Events are lines prefixed with `data:`, separated by a blank line.
- Multiple data lines per event are allowed; clients concatenate them. We emit one JSON per event line.
- Keep‑alive: leave connection open; send periodic heartbeat comments if needed. Disable proxy buffering for real‑time delivery.
- Client: `EventSource` in browsers auto‑reconnects; you can use Last-Event-ID to resume if you emit it.
- FastAPI: use `StreamingResponse(generator, media_type="text/event-stream")` and ensure the generator yields strings ending with `\n\n`.

## Example (FastAPI + EventSource)

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio, json

app = FastAPI()

async def event_gen():
    for i in range(3):
        yield f"data: {json.dumps({'event':'token','i':i})}\n\n"
        await asyncio.sleep(0.05)

@app.get("/stream")
async def stream():
    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

```javascript
const es = new EventSource("/stream");
es.onmessage = (e) => console.log(JSON.parse(e.data));
```

## Gateway SSE semantics

- Token events:
  - The gateway forwards all `token` events in order as they are produced to enable real‑time incremental rendering.
  - Clients may render tokens incrementally; the final `output` still contains the complete text.
- Output event:
  - The complete assistant text is delivered via a single `output` event at the end of the turn.
  - Clients SHOULD render final text from `output.text`.
- Tool step events:
  - `tool_step` events are forwarded as-is for progress/trace UX.

Notes:

- The `max_events` query parameter on `/stream/{conversation_id}` is intended for testing/tools and not a stability guarantee for public clients.

## References

- MDN SSE overview; FastAPI StreamingResponse; reverse proxy buffering notes
