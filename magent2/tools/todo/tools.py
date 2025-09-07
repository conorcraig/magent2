from __future__ import annotations

import importlib
import os
from typing import Any

import redis

from .models import Task
from .redis_store import RedisTodoStore

# Module-level store cache to avoid repeated reconnects
_STORE: RedisTodoStore | None = None


def _serialize_task(task: Task) -> dict[str, Any]:
    return task.model_dump(mode="json")


def _require_str_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    v = value.strip()
    if not v:
        raise ValueError(f"{name} must be non-empty")
    return v


def _get_store() -> RedisTodoStore:
    global _STORE
    if _STORE is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        prefix = os.getenv("TODO_STORE_PREFIX", "todo")
        _STORE = RedisTodoStore(url=url, key_prefix=prefix)
    return _STORE


def _require_metadata_dict(name: str, value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict")
    return value


# Plain callable tools (used by tests and callers expecting functions)
def create_task_tool(
    conversation_id: str, title: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    cid = _require_str_non_empty("conversation_id", conversation_id)
    ttl = _require_str_non_empty("title", title)
    md = _require_metadata_dict("metadata", metadata)
    try:
        t = _get_store().create_task(conversation_id=cid, title=ttl, metadata=md or {})
        return {"task": _serialize_task(t)}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def get_task_tool(task_id: str) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    try:
        t = _get_store().get_task(tid)
        return {"task": _serialize_task(t)} if t is not None else {"task": None}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def list_tasks_tool(conversation_id: str) -> dict[str, Any]:
    cid = _require_str_non_empty("conversation_id", conversation_id)
    try:
        tasks = _get_store().list_tasks(cid)
        return {"tasks": [_serialize_task(t) for t in tasks]}
    except redis.exceptions.RedisError as e:
        return {"tasks": [], "error": str(e), "transient": True}


def update_task_tool(
    task_id: str,
    *,
    title: str | None = None,
    completed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be a string if provided")
    if title is None and completed is None and metadata is None:
        raise ValueError("no fields to update")
    md = _require_metadata_dict("metadata", metadata)
    try:
        t = _get_store().update_task(tid, title=title, completed=completed, metadata=md)
        return {"task": _serialize_task(t)} if t is not None else {"task": None}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def delete_task_tool(task_id: str) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    try:
        ok = _get_store().delete_task(tid)
        return {"ok": bool(ok)}
    except redis.exceptions.RedisError as e:
        return {"ok": False, "error": str(e), "transient": True}


__all__ = [
    "create_task_tool",
    "get_task_tool",
    "list_tasks_tool",
    "update_task_tool",
    "delete_task_tool",
]


# Optional: expose Agents SDK function tools if the decorator is available
def _maybe_get_function_tool() -> Any | None:
    try:
        module = importlib.import_module("agents")
    except Exception:  # noqa: BLE001
        return None
    return getattr(module, "function_tool", None)


_function_tool = _maybe_get_function_tool()

if _function_tool is not None:

    @_function_tool(strict_mode=False)
    def todo_create(
        conversation_id: str, title: str, metadata: dict[str, Any] | None = None
    ) -> Any:
        """Create a todo task."""
        return create_task_tool(conversation_id, title, metadata)

    @_function_tool
    def todo_get(task_id: str) -> Any:
        """Get a todo task by id."""
        return get_task_tool(task_id)

    @_function_tool
    def todo_list(conversation_id: str) -> Any:
        """List todo tasks for a conversation."""
        return list_tasks_tool(conversation_id)

    @_function_tool(strict_mode=False)
    def todo_update(
        task_id: str,
        *,
        title: str | None = None,
        completed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Update a todo task by id."""
        return update_task_tool(task_id, title=title, completed=completed, metadata=metadata)

    @_function_tool
    def todo_delete(task_id: str) -> Any:
        """Delete a todo task by id."""
        return delete_task_tool(task_id)

    __all__.extend(
        [
            "todo_create",
            "todo_get",
            "todo_list",
            "todo_update",
            "todo_delete",
        ]
    )
