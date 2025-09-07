# Redis Streams (Bus semantics)

- Streams store ordered entries; each entry has a Redis entry id (e.g. 1712345678901-0) and arbitrary field map.
- Append: XADD stream key with fields. We store a canonical UUID in field `id` and the JSON payload in field `payload`.
- Tail reads without groups: XRANGE/XREVRANGE; to read after cursor, seek after a known entry id.
- Consumer groups: XGROUP creates a group at an id (often "0"); XREADGROUP with `>` delivers only new entries to the group.
- Acknowledgement: XACK marks entries as processed for the group; unacked entries show in XPENDING.
- Delivery: At‑least‑once. Consumers must be idempotent and/or dedupe by canonical UUID.
- Cursors:
  - If you track Redis entry ids, you can fetch after that id efficiently.
  - If you track your own UUIDs in a field, you may need a scan to find the corresponding entry id, then continue from there.
- Topics we use:
  - Inbound chat: `chat:{conversation_id}` and `chat:{agent_name}`
  - Streamed events: `stream:{conversation_id}`
  - Control: `control:{agent_name}` (pause/resume etc.)

## Do’s & don’ts

Do
- Use consumer groups for scalable workers; ack after successful processing.
- Keep canonical UUID in the entry fields for idempotency.
- Use tail reads without groups for simple fan‑out streams (SSE topic).

Don’t
- Rely on exactly‑once semantics; plan for at‑least‑once.
- Scan entire streams for every read; keep efficient cursors.

## Example (redis‑py – append and read)

```python
import json, uuid, redis

r = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
topic = "chat:example"

# Append with canonical UUID + JSON payload
bus_id = str(uuid.uuid4())
r.xadd(topic, {"id": bus_id, "payload": json.dumps({"content": "hello"})})

# Tail read last 10 entries (no group)
entries = r.xrevrange(topic, "+", "-", count=10) or []
entries.reverse()
for entry_id, fields in entries:
    payload = json.loads(fields.get("payload", "{}"))
    print(entry_id, fields.get("id"), payload)

# Consumer group read + ack
group, consumer = "g1", "c1"
try:
    r.xgroup_create(topic, group, id="0", mkstream=True)
except Exception as e:
    if "BUSYGROUP" not in str(e):
        raise
resp = r.xreadgroup(groupname=group, consumername=consumer, streams={topic: ">"}, count=10, block=0)
for _, items in (resp or []):
    for entry_id, fields in items:
        # process ...
        r.xack(topic, group, entry_id)
```

## Example (Redis CLI – group setup)

```bash
redis-cli XGROUP CREATE chat:DevAgent g1 0 MKSTREAM
redis-cli XADD chat:DevAgent * id 123 payload '{"content":"hi"}'
redis-cli XREADGROUP GROUP g1 c1 COUNT 10 STREAMS chat:DevAgent >
```

## References
- Redis Streams overview
- XADD/XREADGROUP/XPENDING docs
