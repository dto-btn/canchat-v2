import asyncio

import pytest

from open_webui import tasks as task_registry


@pytest.fixture(autouse=True)
def clear_task_registry():
    task_registry.tasks.clear()
    task_registry.task_metadata_by_id.clear()
    task_registry.recent_task_states.clear()
    yield
    task_registry.tasks.clear()
    task_registry.task_metadata_by_id.clear()
    task_registry.recent_task_states.clear()


@pytest.mark.asyncio
async def test_create_task_records_shared_running_state_before_task_starts(monkeypatch):
    events = []

    async def fake_upsert_shared_task_record(task_id, *, state, metadata=None):
        events.append((state, task_id, metadata))

    async def complete_immediately():
        events.append(("started", None, None))

    monkeypatch.setattr(
        task_registry,
        "upsert_shared_task_record",
        fake_upsert_shared_task_record,
    )

    task_id, task = await task_registry.create_task(
        complete_immediately(),
        metadata={"chat_id": "chat-id"},
    )
    await task
    await asyncio.sleep(0)

    assert events[0] == ("running", task_id, {"chat_id": "chat-id"})
    assert events[1] == ("started", None, None)


@pytest.mark.asyncio
async def test_stop_task_returns_recent_state_before_checking_shared_record(
    monkeypatch,
):
    task_registry.remember_task_state("recent-task", "cancelled")

    async def fail_get_shared_task_record(task_id):
        raise AssertionError(f"shared task lookup should not run for {task_id}")

    monkeypatch.setattr(
        task_registry,
        "get_shared_task_record",
        fail_get_shared_task_record,
    )

    result = await task_registry.stop_task("recent-task")

    assert result == {
        "status": True,
        "message": "Task recent-task already cancelled.",
        "state": "cancelled",
    }


@pytest.mark.asyncio
async def test_stop_task_returns_success_for_recently_completed_task():
    async def complete_immediately():
        return "done"

    task_id, task = await task_registry.create_task(complete_immediately())
    await task

    result = await task_registry.stop_task(task_id)

    assert result == {
        "status": True,
        "message": f"Task {task_id} already completed.",
        "state": "completed",
    }


@pytest.mark.asyncio
async def test_stop_task_raises_for_unknown_task_id():
    with pytest.raises(ValueError, match="Task with ID missing-task not found"):
        await task_registry.stop_task("missing-task")


@pytest.mark.asyncio
async def test_stop_task_caches_terminal_shared_state_locally(monkeypatch):
    async def fake_get_shared_task_record(task_id):
        return {
            "task_id": task_id,
            "state": "completed",
            "owner_instance_id": "remote-instance",
        }

    monkeypatch.setattr(
        task_registry, "get_shared_task_record", fake_get_shared_task_record
    )

    result = await task_registry.stop_task("remote-completed-task")

    assert result == {
        "status": True,
        "message": "Task remote-completed-task already completed.",
        "state": "completed",
    }
    assert task_registry.get_recent_task_state("remote-completed-task") == "completed"


@pytest.mark.asyncio
async def test_stop_task_requests_remote_cancellation(monkeypatch):
    published = []

    async def fake_get_shared_task_record(task_id):
        return {
            "task_id": task_id,
            "state": "running",
            "owner_instance_id": "remote-instance",
        }

    async def fake_publish_remote_stop_request(task_id, owner_instance_id):
        published.append((task_id, owner_instance_id))
        return True

    async def fake_wait_for_task_terminal_state(task_id, timeout_seconds=5):
        assert timeout_seconds == task_registry.REMOTE_STOP_WAIT_SECONDS
        return "cancelled"

    monkeypatch.setattr(
        task_registry, "get_shared_task_record", fake_get_shared_task_record
    )
    monkeypatch.setattr(
        task_registry,
        "publish_remote_stop_request",
        fake_publish_remote_stop_request,
    )
    monkeypatch.setattr(
        task_registry,
        "wait_for_task_terminal_state",
        fake_wait_for_task_terminal_state,
    )

    result = await task_registry.stop_task("remote-task")

    assert published == [("remote-task", "remote-instance")]
    assert result == {
        "status": True,
        "message": "Task remote-task successfully stopped.",
        "state": "cancelled",
    }


@pytest.mark.asyncio
async def test_stop_task_rechecks_recent_state_after_shared_lookup(monkeypatch):
    recent_states = iter([None, "completed"])

    def fake_get_recent_task_state(task_id):
        return next(recent_states, None)

    async def fake_get_shared_task_record(task_id):
        return None

    monkeypatch.setattr(
        task_registry, "get_recent_task_state", fake_get_recent_task_state
    )
    monkeypatch.setattr(
        task_registry, "get_shared_task_record", fake_get_shared_task_record
    )

    result = await task_registry.stop_task("race-task")

    assert result == {
        "status": True,
        "message": "Task race-task already completed.",
        "state": "completed",
    }
