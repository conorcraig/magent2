# Server‑Sent Events (SSE) (Gateway streaming)

- Protocol: HTTP response with `Content-Type: text/event-stream`. Events are lines prefixed with `data: `, separated by a blank line.
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

## References
- MDN SSE overview; FastAPI StreamingResponse; reverse proxy buffering notes
