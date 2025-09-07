from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def tool_env(monkeypatch: pytest.MonkeyPatch, redis_url: str) -> dict[str, str]:
    prefix = f"todotool:{uuid.uuid4()}"
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("TODO_STORE_PREFIX", prefix)
    return {"REDIS_URL": redis_url, "TODO_STORE_PREFIX": prefix}


def test_create_and_get(tool_env: dict[str, str]) -> None:
    from magent2.tools.todo.tools import create_task_tool, get_task_tool

    res = create_task_tool("conv1", "Write tests", {"prio": "high"})
    assert isinstance(res, dict)
    task = res.get("task")
    assert isinstance(task, dict)
    assert task["title"] == "Write tests"
    assert task["conversation_id"] == "conv1"
    assert task["completed"] is False
    assert task["metadata"]["prio"] == "high"
    assert isinstance(task["created_at"], str)

    tid = task["id"]
    got = get_task_tool(tid)
    assert got["task"]["id"] == tid


def test_list_ordering(tool_env: dict[str, str]) -> None:
    from magent2.tools.todo.tools import create_task_tool, list_tasks_tool

    t1 = create_task_tool("conv2", "First")
    t2 = create_task_tool("conv2", "Second")
    t3 = create_task_tool("conv2", "Third")
    ids = [t1["task"]["id"], t2["task"]["id"], t3["task"]["id"]]

    res = list_tasks_tool("conv2")
    got_ids = [x["id"] for x in res["tasks"]]
    assert got_ids == ids


def test_update_and_delete(tool_env: dict[str, str]) -> None:
    from magent2.tools.todo.tools import (
        create_task_tool,
        delete_task_tool,
        get_task_tool,
        update_task_tool,
    )

    created = create_task_tool("conv3", "Edit me")
    tid = created["task"]["id"]

    updated = update_task_tool(tid, title="Edited", completed=True)
    assert updated["task"]["title"] == "Edited"
    assert updated["task"]["completed"] is True

    got = get_task_tool(tid)
    assert got["task"]["completed"] is True

    deleted = delete_task_tool(tid)
    assert deleted["ok"] is True


def test_get_update_missing_returns_null(tool_env: dict[str, str]) -> None:
    from magent2.tools.todo.tools import get_task_tool, update_task_tool

    missing_id = str(uuid.uuid4())
    assert get_task_tool(missing_id)["task"] is None
    assert update_task_tool(missing_id, title="x")["task"] is None


@pytest.mark.parametrize(
    "cid,title,err",
    [
        ("", "x", "conversation_id"),
        ("   ", "x", "conversation_id"),
        ("c", "", "title"),
        ("c", "   ", "title"),
    ],
)
def test_validation_errors_on_create(
    cid: str, title: str, err: str, tool_env: dict[str, str]
) -> None:
    from magent2.tools.todo.tools import create_task_tool

    with pytest.raises(ValueError) as ei:
        create_task_tool(cid, title)
    assert err in str(ei.value)


def test_json_safety_created_at_is_str(tool_env: dict[str, str]) -> None:
    from magent2.tools.todo.tools import create_task_tool

    res = create_task_tool("conv4", "Check json")
    assert isinstance(res["task"]["created_at"], str)

