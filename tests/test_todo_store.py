from __future__ import annotations

import os
import time
import uuid
from collections.abc import Generator

import pytest
import redis

from magent2.tools.todo.redis_store import RedisTodoStore

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _redis_available(url: str) -> bool:
    try:
        client = redis.Redis.from_url(url)
        return bool(client.ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(REDIS_URL),
    reason=("Redis not available. Start it with `docker compose up -d` or set REDIS_URL."),
)


@pytest.fixture()
def key_prefix() -> str:
    return f"testtodo:{uuid.uuid4()}"


@pytest.fixture()
def store(key_prefix: str) -> Generator[RedisTodoStore, None, None]:
    # Import here to keep tests importable before implementation lands
    from magent2.tools.todo.redis_store import RedisTodoStore

    s = RedisTodoStore(url=REDIS_URL, key_prefix=key_prefix)
    yield s


def test_create_and_get_persists_across_instances(store: RedisTodoStore, key_prefix: str) -> None:
    from magent2.tools.todo.redis_store import RedisTodoStore

    t = store.create_task(conversation_id="conv_a", title="Write tests")

    assert t.id
    fetched = store.get_task(t.id)
    assert fetched is not None
    assert fetched.title == "Write tests"
    assert fetched.conversation_id == "conv_a"
    assert fetched.completed is False

    # Recreate store to simulate process restart
    store2 = RedisTodoStore(url=REDIS_URL, key_prefix=key_prefix)
    fetched2 = store2.get_task(t.id)
    assert fetched2 is not None
    assert fetched2.id == t.id


def test_list_ordering_by_created_at(store: RedisTodoStore) -> None:
    # Create spaced tasks to ensure ordering by created_at
    t1 = store.create_task(conversation_id="conv_b", title="First")
    time.sleep(0.01)
    t2 = store.create_task(conversation_id="conv_b", title="Second")
    time.sleep(0.01)
    t3 = store.create_task(conversation_id="conv_b", title="Third")

    tasks = store.list_tasks("conv_b")
    assert [x.id for x in tasks] == [t1.id, t2.id, t3.id]


def test_update_and_complete(store: RedisTodoStore) -> None:
    t = store.create_task(conversation_id="conv_c", title="Edit me")

    updated = store.update_task(t.id, title="Edited", completed=True)
    assert updated is not None
    assert updated.title == "Edited"
    assert updated.completed is True

    fetched = store.get_task(t.id)
    assert fetched is not None
    assert fetched.title == "Edited"
    assert fetched.completed is True


def test_delete_task(store: RedisTodoStore) -> None:
    t = store.create_task(conversation_id="conv_d", title="Remove me")
    ok = store.delete_task(t.id)
    assert ok is True
    assert store.get_task(t.id) is None
    assert all(x.id != t.id for x in store.list_tasks("conv_d"))


def test_metadata_persists(store: RedisTodoStore) -> None:
    t = store.create_task(
        conversation_id="conv_e",
        title="With meta",
        metadata={"priority": "high", "tags": ["a", "b"]},
    )
    got = store.get_task(t.id)
    assert got is not None
    assert got.metadata["priority"] == "high"
    assert got.metadata["tags"] == ["a", "b"]
