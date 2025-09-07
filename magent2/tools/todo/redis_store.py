from __future__ import annotations

import json
from typing import cast

import redis

from .models import Task


class TodoStore:
    """Pluggable Todo store interface.

    Concrete implementations should provide CRUD operations and listing by
    conversation ordered by creation time.
    """

    def create_task(
        self, *, conversation_id: str, title: str, metadata: dict | None = None
    ) -> Task:  # pragma: no cover - interface only
        raise NotImplementedError

    def get_task(self, task_id: str) -> Task | None:  # pragma: no cover - interface only
        raise NotImplementedError

    def list_tasks(self, conversation_id: str) -> list[Task]:  # pragma: no cover - interface only
        raise NotImplementedError

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        completed: bool | None = None,
        metadata: dict | None = None,
    ) -> Task | None:  # pragma: no cover - interface only
        raise NotImplementedError

    def delete_task(self, task_id: str) -> bool:  # pragma: no cover - interface only
        raise NotImplementedError


class RedisTodoStore(TodoStore):
    """Redis-backed Todo store.

    Data structures:
    - Hash per task: key `{prefix}:task:{id}` with fields `json`
    - Sorted set per conversation for ordering by `created_at` timestamp:
      key `{prefix}:conv:{conversation_id}` with score=created_at epoch seconds, member=task_id
    """

    def __init__(self, *, url: str, key_prefix: str = "todo") -> None:
        self._redis: redis.Redis = redis.Redis.from_url(url)
        self._prefix = key_prefix.rstrip(":")

    # key helpers
    def _task_key(self, task_id: str) -> str:
        return f"{self._prefix}:task:{task_id}"

    def _conv_key(self, conversation_id: str) -> str:
        return f"{self._prefix}:conv:{conversation_id}"

    def create_task(
        self, *, conversation_id: str, title: str, metadata: dict | None = None
    ) -> Task:
        task = Task(conversation_id=conversation_id, title=title, metadata=metadata or {})
        data = task.model_dump()
        payload = json.dumps(data, separators=(",", ":"))
        p = self._redis.pipeline()
        p.hset(self._task_key(task.id), mapping={"json": payload})
        p.zadd(self._conv_key(conversation_id), {task.id: task.created_at.timestamp()})
        p.execute()
        return task

    def get_task(self, task_id: str) -> Task | None:
        raw = cast(bytes | None, self._redis.hget(self._task_key(task_id), "json"))
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
            return Task.model_validate(data)
        except Exception:
            return None

    def list_tasks(self, conversation_id: str) -> list[Task]:
        ids_bytes = cast(list[bytes], self._redis.zrange(self._conv_key(conversation_id), 0, -1))
        if not ids_bytes:
            return []
        task_ids = [b.decode("utf-8") for b in ids_bytes]
        result: list[Task] = []
        for tid in task_ids:
            t = self.get_task(tid)
            if t is not None:
                result.append(t)
        return result

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        completed: bool | None = None,
        metadata: dict | None = None,
    ) -> Task | None:
        current = self.get_task(task_id)
        if current is None:
            return None
        if title is not None:
            current.title = title
        if completed is not None:
            current.completed = completed
        if metadata is not None:
            current.metadata = metadata
        payload = json.dumps(current.model_dump(), separators=(",", ":"))
        self._redis.hset(self._task_key(task_id), mapping={"json": payload})
        return current

    def delete_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        p = self._redis.pipeline()
        p.delete(self._task_key(task_id))
        p.zrem(self._conv_key(task.conversation_id), task_id)
        res = p.execute()
        # Return True if either deletion removed something
        return bool(sum(int(x) for x in res))


__all__ = ["TodoStore", "RedisTodoStore"]
