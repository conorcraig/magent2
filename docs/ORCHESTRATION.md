# Orchestration (signals, registry, branches)

## Overview

- Single agent type; orchestration is expressed via tools. No separate orchestrator class.
- Parent agent can fan out work to child conversations, coordinate with signals, and fan in when children finish.
- File-scope and responsibilities are conveyed via metadata and the `TeamRegistry`.
- Branches are used for isolation and naming; worktrees are not used.

## Environment flags

- `AUTO_CHILD_SIGNAL_DONE=1`
  - When a child message contains `metadata.orchestrate.done_topic`, the worker emits a `signal:...:done` automatically after the run completes.
- `ENABLE_ORCHESTRATE_TOOL=1`
  - Registers the `orchestrate_split_tool` as an Agents SDK function tool. Keep disabled if your SDK schema must remain stable.

## Minimal usage

### Split work and publish child tasks

```python
from magent2.tools.orchestrate import orchestrate_split

# Fan out into two child conversations targeting a specific agent name
res = orchestrate_split(
    task="Implement feature X",
    num_children=2,
    responsibilities=["build"],
    allowed_paths=["src/app/**"],
    wait=False,                 # don't block; use signals for fan-in
    target_agent="AgentX",
    timeout_ms=2_000,
)
assert res["ok"] is True
print(res["children"])  # list of child conversation ids
print(res["topics"])    # corresponding signal topics to wait on
```

- Each published message includes structured `metadata.orchestrate`:
  - `responsibilities`, `allowed_paths`, and a unique `done_topic`.
- If `AUTO_CHILD_SIGNAL_DONE=1`, the worker emits a `signal:...:done` after the child finishes.

### Wait for children to complete (fan-in)

Use the signals tool directly (example shape):

```python
from magent2.tools.signals import signal_wait

last_id = None
for topic in res["topics"]:
    # Poll each topic; production code should use timeouts/backoff
    ok = False
    while not ok:
        out = signal_wait(topic=topic, last_id=last_id, timeout_ms=1000)
        ok = out.get("ok", False)
```

Notes:

- Prefer a loop with `last_id` to preserve ordering and avoid lost wakeups.
- For N children, a simple approach is to wait on each `topic` until you observe one message, then proceed.

## Team registry and path ownership

`TeamRegistry` records responsibilities and `allowed_paths`. Use it to determine ownership for files and to surface a window person for escalation.

- Register/update agents at runtime (in-memory) or via your own bootstrap code.
- Path resolution uses normalized POSIX-style paths and simple glob matching; the most specific match wins.

See: `magent2/team/registry.py`

## Branch allocation

When isolation is needed, use the branch allocator to create/switch branches with a safe naming scheme.

```python
from magent2.team.branch import allocate_branch

branch = allocate_branch(repo_root=".", agent_name="DevAgent", ticket="146")
print(branch.name)  # e.g., feature/146/DevAgent
```

- Branch convention: `feature/{ticket or 'task'}/{agent_name}`
- Worktrees are intentionally not used.

## Safety and policy (concise)

- Terminal tool is deny-by-default; configure allowlist and limits via env (see README: Terminal tool section).
- Keep payloads for signals small and machine-oriented.
- Ensure `allowed_paths` reflect the intended scope; tools should respect these when reading/writing.

## Enable the function tool (optional)

If you want to expose an Agents SDK function tool for splitting work from within the model:

```bash
export ENABLE_ORCHESTRATE_TOOL=1
```

Keep this disabled if you rely on a stable, previously-declared function schema during testing.
