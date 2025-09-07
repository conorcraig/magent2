### Handover: Expose Todo tool as Agents SDK function tools (Issue #35)

#### Context
- Goal: Expose CRUD + list operations as OpenAI Agents SDK function tools backed by `RedisTodoStore`.
- Scope limited to `magent2/tools/todo/` with tests under `tests/`.
- Do not change frozen v1 contracts (envelope, bus). See `docs/CONTRACTS.md`.
- Reuse Redis fixtures from `tests/conftest.py` and existing `RedisTodoStore` tests.

#### Requirements
- Define Agents SDK function tools for: create, get, list, update, delete.
- Validate inputs; use `Task` model for (de)serialization; ensure JSON-safe outputs.
- Read `REDIS_URL` from environment (default from README/compose) and handle transient Redis errors.
- Pass `just check` locally: ruff, mypy, complexity, secrets, pytest.

#### References
- OpenAI Agents SDK docs: see `docs/refs/openai-agents-sdk.md`.
- Redis Streams (bus semantics, resilience expectations): `docs/refs/redis-streams.md`.
- Store + model: `magent2/tools/todo/redis_store.py`, `magent2/tools/todo/models.py`.

---

### Design

#### File layout
- Add `magent2/tools/todo/tools.py` exporting 5 function tools and small helpers.
  - Public exports: `create_task_tool`, `get_task_tool`, `list_tasks_tool`, `update_task_tool`, `delete_task_tool`.
  - Internal helpers: `_get_store()`, `_serialize_task(task: Task) -> dict`, `_validate_*`.

#### Tool API shapes (JSON-safe)
- Common: return dicts with explicit keys; never return Pydantic models directly.
- Create
  - Input: `conversation_id: str`, `title: str`, `metadata: dict | None = None`.
  - Output: `{ "task": { ...Task JSON... } }`.
- Get
  - Input: `task_id: str`.
  - Output: `{ "task": { ... } }` if found; `{ "task": null }` if not found.
- List
  - Input: `conversation_id: str`.
  - Output: `{ "tasks": [ { ... }, ... ] }` ordered by `created_at` ascending.
- Update
  - Input: `task_id: str`, optional `title: str | None`, `completed: bool | None`, `metadata: dict | None`.
  - Output: `{ "task": { ... } }` if found; `{ "task": null }` if not found.
- Delete
  - Input: `task_id: str`.
  - Output: `{ "ok": bool }`.

Notes:
- Task serialization via `Task.model_dump(mode="json")` to ensure RFC3339 `created_at` and JSON-safe types.
- Inputs validated for non-empty strings and basic type checks before store calls; deeper shape validation relies on type hints + Agents SDK schema.

#### Environment/config
- `REDIS_URL`: read from env, fallback `redis://localhost:6379/0` (matches README and compose behavior via `RedisBus`).
- `TODO_STORE_PREFIX`: optional env to override Redis key prefix (default `todo`). Use in tests to isolate state.

#### Store access
- `_get_store()`:
  - Reads `REDIS_URL` and `TODO_STORE_PREFIX` envs.
  - Returns `RedisTodoStore(url=REDIS_URL, key_prefix=prefix)`.

#### Error handling
- Wrap store calls with `try/except` catching `redis.exceptions.RedisError`.
  - Return `{ "ok": false, "error": "<message>", "transient": true }` for delete; for other tools, `{ "task": null, "error": "...", "transient": true }` or `{ "tasks": [], ... }` as appropriate.
- Input validation errors raise `ValueError` to be surfaced by Agents SDK as tool errors; message should be concise (e.g., "title must be non-empty").

#### Function tool decorator
- Use the OpenAI Agents SDK `function_tool` decorator to register tools with schemas inferred from Python type hints.
- Import path: verify in `docs/refs/openai-agents-sdk.md` (typical: `from openai_agents import function_tool`).

---

### Implementation plan
1) Create `magent2/tools/todo/tools.py` with:
   - `_serialize_task(task: Task) -> dict` using `model_dump(mode="json")`.
   - `_get_store()` reading env and returning `RedisTodoStore`.
   - Validators: `_require_str(name, value)`, `_require_non_empty(name, value)`.
   - Five tool functions decorated with `@function_tool(name=..., description=...)`.
   - Type hints for precise JSON schema generation.

2) Tests in `tests/test_todo_tools.py` (unit-style, no Agents runner):
   - Fixture `tool_env(monkeypatch, redis_url)` sets `REDIS_URL` and unique `TODO_STORE_PREFIX`.
   - Test create/get/list/update/delete happy paths.
   - Test get/update on missing task return `{ "task": null }`.
   - Test minimal validation errors (empty title, empty conversation_id) raise `ValueError`.
   - Test JSON-safety: `created_at` is string; metadata round-trips.

3) Optional: tiny smoke that tools import without importing Redis at collection time (store created lazily in `_get_store()`).

4) Run `just check` and fix any lint/mypy issues (keep functions small; match ruff/mypy rules in `pyproject.toml`).

---

### Example (sketch)
Note: This illustrates shapes/flow; confirm exact `function_tool` import per SDK docs.

```python
from __future__ import annotations
import os
from typing import Any
from openai_agents import function_tool  # confirm path in refs
from magent2.tools.todo.models import Task
from magent2.tools.todo.redis_store import RedisTodoStore

def _serialize_task(task: Task) -> dict[str, Any]:
    return task.model_dump(mode="json")

def _get_store() -> RedisTodoStore:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    prefix = os.getenv("TODO_STORE_PREFIX", "todo")
    return RedisTodoStore(url=url, key_prefix=prefix)

@function_tool(name="todo_create", description="Create a todo task")
def create_task_tool(conversation_id: str, title: str, metadata: dict[str, Any] | None = None) -> dict:
    if not conversation_id or not conversation_id.strip():
        raise ValueError("conversation_id must be non-empty")
    if not title or not title.strip():
        raise ValueError("title must be non-empty")
    t = _get_store().create_task(conversation_id=conversation_id.strip(), title=title.strip(), metadata=metadata or {})
    return {"task": _serialize_task(t)}

# ... similarly: get/list/update/delete with outputs described above
```

---

### Risks & mitigations
- Import path for `function_tool` may differ across SDK versions ⇒ verify against `docs/refs/openai-agents-sdk.md`; add a narrow adapter import.
- Redis connectivity/transient errors ⇒ catch and mark `transient: true`; callers can retry.
- Test isolation ⇒ use `TODO_STORE_PREFIX` per-test to avoid cross-test interference.

### Done when
- Tools implemented and exported; tests cover CRUD/list; `just check` passes.
- No changes to frozen contracts; only `magent2/tools/todo/` and tests updated.

### Next steps for implementer
1) Implement `magent2/tools/todo/tools.py` per design.
2) Add `tests/test_todo_tools.py` with fixtures and cases above.
3) Run `just check`; fix any issues; open PR referencing #35.
