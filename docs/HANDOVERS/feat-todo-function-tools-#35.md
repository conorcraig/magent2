### Handover: Expose Todo tool as Agents SDK function tools (Issue #35)

#### Context
- Goal: Expose CRUD + list operations as OpenAI Agents SDK function tools backed by `RedisTodoStore`.
- Scope limited to `magent2/tools/todo/` and tests under `tests/`.
- Do not change frozen v1 contracts (envelope, bus). See `docs/CONTRACTS.md`.
- Reuse Redis fixtures from `tests/conftest.py` and the store tests in `tests/test_todo_store.py`.

#### Requirements
- Define Agents SDK function tools for: create, get, list, update, delete.
- Validate inputs; use `Task` model for (de)serialization; ensure JSON-safe outputs.
- Read `REDIS_URL` from environment; handle transient Redis errors gracefully.
- Pass `just check` locally: ruff, mypy, complexity, secrets, pytest.

#### References (offline)
- OpenAI Agents SDK quick links and cheats: `docs/refs/openai-agents-sdk.md` (contains import paths and examples usable offline).
- Redis Streams bus notes: `docs/refs/redis-streams.md`.
- Store + model: `magent2/tools/todo/redis_store.py`, `magent2/tools/todo/models.py`.

---

### Design

#### File layout
- Add `magent2/tools/todo/tools.py` exporting five function tools and helpers.
  - Public exports: `create_task_tool`, `get_task_tool`, `list_tasks_tool`, `update_task_tool`, `delete_task_tool`.
  - Internal helpers: `_get_store()`, `_serialize_task(task: Task) -> dict`, simple validators.

#### Tool APIs (inputs/outputs)
- Common: return dicts with explicit keys; never return Pydantic models directly.

- Create
  - Input: `conversation_id: str`, `title: str`, `metadata: dict | None = None`.
  - Output: `{ "task": { ...Task JSON... } }`.

- Get
  - Input: `task_id: str`.
  - Output: `{ "task": { ... } }` if found; `{ "task": null }` otherwise.

- List
  - Input: `conversation_id: str`.
  - Output: `{ "tasks": [ { ... }, ... ] }` ordered by `created_at` ascending.

- Update
  - Input: `task_id: str`, optional `title: str | None`, `completed: bool | None`, `metadata: dict | None`.
  - Output: `{ "task": { ... } }` if found; `{ "task": null }` otherwise.

- Delete
  - Input: `task_id: str`.
  - Output: `{ "ok": bool }`.

Notes:
- Task serialization: `Task.model_dump(mode="json")` to ensure RFC3339 `created_at` and JSON-safe types.
- Inputs validated for non-empty strings and basic types before store calls. More detailed schema comes from Python type hints → Agents SDK infers JSON schema.

#### Environment/config
- `REDIS_URL`: read from env, fallback `redis://localhost:6379/0` (matches README/compose defaults).
- `TODO_STORE_PREFIX`: optional env to override Redis key prefix (default `todo`) to support test isolation.

#### Store access
- `_get_store()`:
  - Reads `REDIS_URL` and `TODO_STORE_PREFIX` envs.
  - Returns `RedisTodoStore(url=..., key_prefix=...)`.

#### Error handling
- Catch `redis.exceptions.RedisError` around store calls.
  - For delete: return `{ "ok": false, "error": "<message>", "transient": true }`.
  - For get/update: `{ "task": null, "error": "...", "transient": true }`.
  - For list: `{ "tasks": [], "error": "...", "transient": true }`.
  - For create: `{ "task": null, "error": "...", "transient": true }`.
- Input validation errors raise `ValueError` with concise messages (Agents SDK will surface them as tool errors).

#### Function tool decorator (Agents SDK)
- Import and decorate using the offline cheatsheet in `docs/refs/openai-agents-sdk.md`:
  - `from agents import function_tool`
  - Decorate functions with `@function_tool(name="...", description="...")` (name/description optional; name defaults to function name).
  - Use precise type hints so the SDK emits a correct JSON schema for tool inputs.

---

### Implementation plan
1) Create `magent2/tools/todo/tools.py` with:
   - `_serialize_task(task: Task) -> dict[str, Any]` using `model_dump(mode="json")`.
   - `_get_store()` reading env and returning `RedisTodoStore`.
   - Validators: `_require_str_non_empty(name: str, value: str) -> str`.
   - Five tool functions decorated with `@function_tool(name=..., description=...)`.

2) Tests in `tests/test_todo_tools.py`:
   - Fixture `tool_env(monkeypatch, redis_url)` sets `REDIS_URL` and unique `TODO_STORE_PREFIX`.
   - Test create/get/list/update/delete happy paths.
   - Test missing task results (`get`/`update`): `{ "task": null }`.
   - Test validation errors (empty title/empty conversation_id) raise `ValueError`.
   - Test JSON-safety: `created_at` is string; metadata round-trips.

3) Ensure lazy store construction to keep import-time light.

4) Run `just check`; address any lint/mypy findings.

---

### Example (shape sketch)

```python
from __future__ import annotations
import os
from typing import Any
from agents import function_tool  # see refs file for offline imports
from magent2.tools.todo.models import Task
from magent2.tools.todo.redis_store import RedisTodoStore

def _serialize_task(task: Task) -> dict[str, Any]:
    return task.model_dump(mode="json")

def _get_store() -> RedisTodoStore:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    prefix = os.getenv("TODO_STORE_PREFIX", "todo")
    return RedisTodoStore(url=url, key_prefix=prefix)

@function_tool(name="todo_create", description="Create a todo task")
def create_task_tool(conversation_id: str, title: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not conversation_id or not conversation_id.strip():
        raise ValueError("conversation_id must be non-empty")
    if not title or not title.strip():
        raise ValueError("title must be non-empty")
    t = _get_store().create_task(conversation_id=conversation_id.strip(), title=title.strip(), metadata=metadata or {})
    return {"task": _serialize_task(t)}

# Similar for get/list/update/delete with outputs described above.
```

---

### Risks & mitigations
- SDK import name/version drift → use `from agents import function_tool` (pinned via `pyproject.toml`), documented in refs.
- Redis connectivity/transient errors → catch and mark `transient: true`; allow caller retry.
- Test isolation → per-test `TODO_STORE_PREFIX` prevents collisions.

### Acceptance
- Tools implemented and exported; tests cover CRUD/list; `just check` passes.
- No changes to frozen contracts.

### Next steps for implementer
1) Implement `magent2/tools/todo/tools.py` per design.
2) Add `tests/test_todo_tools.py` with fixtures/cases above.
3) Run `just check`; fix issues; open PR referencing #35.
