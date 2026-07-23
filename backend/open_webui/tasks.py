import asyncio
from typing import Any

from open_webui.tasking import (
    ACTIVE_TASK_TTL_SECONDS,
    RECENT_TASK_TTL_SECONDS,
    REMOTE_STOP_POLL_INTERVAL_SECONDS,
    REMOTE_STOP_WAIT_SECONDS,
    TASK_COORDINATION_DISTRIBUTED,
    TASK_COORDINATION_URL,
    TASK_INSTANCE_ID,
    SharedTaskState,
    TaskState,
    TERMINAL_TASK_STATES,
    get_task_manager,
    reset_task_manager,
)


def prune_recent_task_states():
    get_task_manager().prune_recent_terminal_records()


def remember_task_state(task_id: str, state: TaskState):
    get_task_manager().remember_task_state(task_id, state)


def get_recent_task_state(task_id: str):
    return get_task_manager().get_recent_task_state(task_id)


async def get_shared_task_record(task_id: str):
    return await get_task_manager().load_record_dict(task_id)


async def upsert_shared_task_record(
    task_id: str,
    *,
    state: SharedTaskState,
    metadata: dict[str, Any] | None = None,
):
    return await get_task_manager().upsert_record(
        task_id,
        state=state,
        metadata=metadata,
    )


async def publish_remote_stop_request(task_id: str, owner_instance_id: str):
    return await get_task_manager().request_remote_stop(task_id, owner_instance_id)


async def wait_for_task_terminal_state(
    task_id: str,
    timeout_seconds: float = REMOTE_STOP_WAIT_SECONDS,
):
    return await get_task_manager().wait_for_terminal_state(task_id, timeout_seconds)


async def cleanup_task(task_id: str, task: asyncio.Task):
    return await get_task_manager().cleanup(task_id, task)


async def create_task(coroutine, metadata: dict[str, Any] | None = None):
    return await get_task_manager().create(coroutine, metadata=metadata)


def get_task(task_id: str):
    return get_task_manager().get_task(task_id)


def list_tasks():
    return get_task_manager().list()


async def start_task_cancellation_listener():
    await get_task_manager().start()


async def stop_task_cancellation_listener():
    await get_task_manager().close()


async def stop_task(task_id: str):
    return await get_task_manager().stop(task_id)


__all__ = [
    "ACTIVE_TASK_TTL_SECONDS",
    "RECENT_TASK_TTL_SECONDS",
    "REMOTE_STOP_POLL_INTERVAL_SECONDS",
    "REMOTE_STOP_WAIT_SECONDS",
    "TASK_COORDINATION_DISTRIBUTED",
    "TASK_COORDINATION_URL",
    "TASK_INSTANCE_ID",
    "TaskState",
    "SharedTaskState",
    "TERMINAL_TASK_STATES",
    "prune_recent_task_states",
    "remember_task_state",
    "get_recent_task_state",
    "get_shared_task_record",
    "upsert_shared_task_record",
    "publish_remote_stop_request",
    "wait_for_task_terminal_state",
    "cleanup_task",
    "create_task",
    "get_task",
    "list_tasks",
    "start_task_cancellation_listener",
    "stop_task_cancellation_listener",
    "stop_task",
    "reset_task_manager",
]
