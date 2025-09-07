# Observability (traces, logs, metrics)

- Correlate `conversation_id` and per‑run `run_id` across Worker/Runner/Tools.
- Tracing: start a span for Worker run; child spans for tool calls; propagate context.
- Logging: JSON logs with minimal stable keys (timestamp, level, message, run_id, conversation_id, agent, tool). Avoid secrets.
- Metrics: counters for runs started/completed/errored; tool calls; retries; DLQ size.

## Example (minimal JSON logging with correlation)

```python
import json, logging, sys

class JsonHandler(logging.StreamHandler):
    def emit(self, record):
        msg = {
            "level": record.levelname,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "conversation_id": getattr(record, "conversation_id", None),
        }
        sys.stdout.write(json.dumps(msg) + "\n")

logger = logging.getLogger("magent2")
logger.setLevel(logging.INFO)
logger.addHandler(JsonHandler())

logger.info("run_started", extra={"run_id": "r1", "conversation_id": "c1"})
```

## References
- OpenTelemetry Python; Python logging cookbook
