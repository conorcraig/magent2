# Safe subprocess (Terminal tool)

- Never use `shell=True`; build argv with `shlex.split` and pass to `Popen`.
- Environment: start from a minimal map; set safe `PATH`; inject only explicit allowlisted env vars.
- Policy: enforce command allowlist (by basename), wall‑clock timeout, output byte cap; non‑interactive (no stdin).
- Termination: on timeout, kill the entire process group (e.g., `os.killpg`) and drain pipes.
- Sandbox: optional working directory sandbox; canonicalize `cwd`/paths; deny path escapes if policy requires.

## Example (timeout + process‑group kill)

```python
import os, shlex, signal
from subprocess import Popen, DEVNULL, PIPE, TimeoutExpired

argv = shlex.split("echo hello")
env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
proc = Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True, start_new_session=True, env=env)
try:
    out, err = proc.communicate(timeout=2)
except TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    out, err = proc.communicate()
print(out)
```

## References

- Python subprocess docs; OWASP Command Injection
