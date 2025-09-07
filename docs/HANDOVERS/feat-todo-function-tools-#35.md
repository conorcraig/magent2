# Handover: Expose Todo function tools (#35)

## Context

- Goal: Expose CRUD + list operations as OpenAI Agents SDK function tools backed by `RedisTodoStore`.
- Scope limited to `magent2/tools/todo/` and tests under `tests/`.
- Do not change frozen v1 contracts (envelope, bus). See `docs/CONTRACTS.md`.
- Reuse Redis fixtures from `tests/conftest.py` and the store tests in `tests/test_todo_store.py`.

## Requirements

- Define Agents SDK function tools for: create, get, list, update, delete.
- Validate inputs; use `Task` model for (de)serialization; ensure JSON-safe outputs.
- Read `REDIS_URL` from environment; handle transient Redis errors gracefully.
- Pass `just check` locally: ruff, mypy, complexity, secrets, pytest.

## References (offline)

- OpenAI Agents SDK quick links and cheats: `docs/refs/openai-agents-sdk.md` (contains import paths and examples usable offline).
- Redis Streams bus notes: `docs/refs/redis-streams.md`.
- Store + model: `magent2/tools/todo/redis_store.py`, `magent2/tools/todo/models.py`.

---

## Design

### File layout

- Add `magent2/tools/todo/tools.py` exporting five function tools and helpers.
  - Public exports: `create_task_tool`, `get_task_tool`, `list_tasks_tool`, `update_task_tool`, `delete_task_tool`.
  - Internal helpers: `_get_store()`, `_serialize_task(task: Task) -> dict`, simple validators.

### Tool APIs (inputs/outputs)

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

### Environment/config

- `REDIS_URL`: read from env, fallback `redis://localhost:6379/0` (matches README/compose defaults).
- `TODO_STORE_PREFIX`: optional env to override Redis key prefix (default `todo`) to support test isolation.

### Store access

- `_get_store()`:
  - Reads `REDIS_URL` and `TODO_STORE_PREFIX` envs.
  - Returns `RedisTodoStore(url=..., key_prefix=...)`.

### Error handling

- Catch `redis.exceptions.RedisError` around store calls.
  - For delete: return `{ "ok": false, "error": "<message>", "transient": true }`.
  - For get/update: `{ "task": null, "error": "...", "transient": true }`.
  - For list: `{ "tasks": [], "error": "...", "transient": true }`.
  - For create: `{ "task": null, "error": "...", "transient": true }`.
- Input validation errors raise `ValueError` with concise messages (Agents SDK will surface them as tool errors).

### Function tool decorator (Agents SDK)

- Import and decorate using the offline cheatsheet in `docs/refs/openai-agents-sdk.md`.
- Keep decorator wrappers thin; core logic in plain functions for easy testing.
