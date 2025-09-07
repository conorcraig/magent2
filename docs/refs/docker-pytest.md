# Docker + pytest‑docker (E2E)

- Compose healthcheck gates service readiness; tests should wait until responsive (e.g., HTTP /health) before proceeding.
- Avoid fixed host ports in tests; discover host port via `docker_services.port_for(service, internal_port)`.
- Keep a single compose file as source of truth; parameterize ports with env vars for local pinning if needed.

## Example (responsive wait & dynamic port)

```python
def is_up(url: str) -> bool: ...

def test_stack(docker_services):
    port = docker_services.port_for("gateway", 8000)
    docker_services.wait_until_responsive(
        timeout=60.0, pause=0.5,
        check=lambda: is_up(f"http://localhost:{port}/health"),
    )
```

## References
- pytest‑docker docs; Compose healthcheck docs
