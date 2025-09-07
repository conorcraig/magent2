from __future__ import annotations

import os
from typing import Any

import redis
from agents import function_tool

from .models import Task
from .redis_store import RedisTodoStore


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
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    prefix = os.getenv("TODO_STORE_PREFIX", "todo")
    return RedisTodoStore(url=url, key_prefix=prefix)


def _create_task_tool(
    conversation_id: str, title: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    cid = _require_str_non_empty("conversation_id", conversation_id)
    ttl = _require_str_non_empty("title", title)
    try:
        t = _get_store().create_task(conversation_id=cid, title=ttl, metadata=metadata or {})
        return {"task": _serialize_task(t)}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def _get_task_tool(task_id: str) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    try:
        t = _get_store().get_task(tid)
        return {"task": _serialize_task(t)} if t is not None else {"task": None}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def _list_tasks_tool(conversation_id: str) -> dict[str, Any]:
    cid = _require_str_non_empty("conversation_id", conversation_id)
    try:
        tasks = _get_store().list_tasks(cid)
        return {"tasks": [_serialize_task(t) for t in tasks]}
    except redis.exceptions.RedisError as e:
        return {"tasks": [], "error": str(e), "transient": True}


def _update_task_tool(
    task_id: str,
    *,
    title: str | None = None,
    completed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be a string if provided")
    try:
        t = _get_store().update_task(tid, title=title, completed=completed, metadata=metadata)
        return {"task": _serialize_task(t)} if t is not None else {"task": None}
    except redis.exceptions.RedisError as e:
        return {"task": None, "error": str(e), "transient": True}


def _delete_task_tool(task_id: str) -> dict[str, Any]:
    tid = _require_str_non_empty("task_id", task_id)
    try:
        ok = _get_store().delete_task(tid)
        return {"ok": bool(ok)}
    except redis.exceptions.RedisError as e:
        return {"ok": False, "error": str(e), "transient": True}


from typing import Callable, Protocol, cast

# Decorated, exported tools with precise callable types for mypy
class _CreateTaskTool(Protocol):
    def __call__(
        self, conversation_id: str, title: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


class _GetTaskTool(Protocol):
    def __call__(self, task_id: str) -> dict[str, Any]: ...


class _ListTasksTool(Protocol):
    def __call__(self, conversation_id: str) -> dict[str, Any]: ...


class _UpdateTaskTool(Protocol):
    def __call__(
        self,
        task_id: str,
        *,
        title: str | None = None,
        completed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _DeleteTaskTool(Protocol):
    def __call__(self, task_id: str) -> dict[str, Any]: ...


create_task_tool = cast(
    _CreateTaskTool,
    function_tool(name_override="todo_create", description_override="Create a todo task")(
        _create_task_tool
    ),
)

get_task_tool = cast(
    _GetTaskTool,
    function_tool(name_override="todo_get", description_override="Get a todo task by id")( 
        _get_task_tool
    ),
)

list_tasks_tool = cast(
    _ListTasksTool,
    function_tool(
        name_override="todo_list",
        description_override="List todo tasks for a conversation",
    )(_list_tasks_tool),
)

update_task_tool = cast(
    _UpdateTaskTool,
    function_tool(name_override="todo_update", description_override="Update a todo task by id")(
        _update_task_tool
    ),
)

delete_task_tool = cast(
    _DeleteTaskTool,
    function_tool(name_override="todo_delete", description_override="Delete a todo task by id")(
        _delete_task_tool
    ),
)

__all__ = ["create_task_tool", "get_task_tool", "list_tasks_tool", "update_task_tool", "delete_task_tool"]

