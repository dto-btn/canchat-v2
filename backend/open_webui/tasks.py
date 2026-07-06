# tasks.py
import asyncio
import json
import logging
import os
import time
from functools import partial
from typing import Any, Literal
from uuid import uuid4

import redis.asyncio as redis

from open_webui.env import (
    REDIS_URL,
    USE_REDIS_LOCKS,
    WEBSOCKET_MANAGER,
    WEBSOCKET_REDIS_URL,
)

# A dictionary to keep track of active tasks
tasks: dict[str, asyncio.Task] = {}
task_metadata_by_id: dict[str, dict[str, Any]] = {}
recent_task_states: dict[str, tuple["TaskState", float]] = {}

RECENT_TASK_TTL_SECONDS = 30
ACTIVE_TASK_TTL_SECONDS = 60 * 60
REMOTE_STOP_WAIT_SECONDS = 5
REMOTE_STOP_POLL_INTERVAL_SECONDS = 0.1

TaskState = Literal["completed", "cancelled", "failed"]
SharedTaskState = Literal["running", "completed", "cancelled", "failed"]
TERMINAL_TASK_STATES = frozenset({"completed", "cancelled", "failed"})

TASKS_REDIS_ENABLED = USE_REDIS_LOCKS or WEBSOCKET_MANAGER == "redis"
TASKS_REDIS_URL = WEBSOCKET_REDIS_URL if WEBSOCKET_MANAGER == "redis" else REDIS_URL
TASK_RECORD_KEY_PREFIX = "open-webui:tasks"
TASK_CANCEL_CHANNEL_PREFIX = "open-webui:tasks:cancel"
TASK_INSTANCE_ID = (
    f"{os.environ.get('HOSTNAME') or 'local'}:{os.getpid()}:{uuid4().hex[:8]}"
)

_task_redis: redis.Redis | None = None
_task_cancel_listener: asyncio.Task | None = None

log = logging.getLogger(__name__)


def prune_recent_task_states():
    """
    Drop terminal task state records after a short retention window.
    """
    now = time.monotonic()
    expired_task_ids = [
        task_id
        for task_id, (_, finished_at) in recent_task_states.items()
        if now - finished_at > RECENT_TASK_TTL_SECONDS
    ]

    for task_id in expired_task_ids:
        recent_task_states.pop(task_id, None)


def _task_record_key(task_id: str):
    return f"{TASK_RECORD_KEY_PREFIX}:{task_id}"


def _task_cancel_channel(instance_id: str):
    return f"{TASK_CANCEL_CHANNEL_PREFIX}:{instance_id}"


def _resolve_task_state(task: asyncio.Task) -> TaskState:
    if task.cancelled():
        return "cancelled"

    if task.exception() is not None:
        return "failed"

    return "completed"


def _build_stop_result(task_id: str, state: str, stop_requested: bool = False):
    if state == "failed":
        return {
            "status": False,
            "message": f"Task {task_id} failed before it could be stopped.",
            "state": "failed",
        }

    if state == "cancelled":
        return {
            "status": True,
            "message": (
                f"Task {task_id} successfully stopped."
                if stop_requested
                else f"Task {task_id} already cancelled."
            ),
            "state": "cancelled",
        }

    return {
        "status": True,
        "message": f"Task {task_id} already completed.",
        "state": "completed",
    }


def _get_recent_stop_result(task_id: str):
    task_state = get_recent_task_state(task_id)
    if task_state is None:
        return None

    return _build_stop_result(task_id, task_state)


def _get_task_record_stop_result(task_id: str, task_record: dict[str, Any]):
    task_state = task_record.get("state")
    if task_state not in TERMINAL_TASK_STATES:
        return None

    remember_task_state(task_id, task_state)
    return _build_stop_result(task_id, task_state)


async def _get_task_redis_client():
    """Lazily create the Redis client used for shared task coordination."""
    global _task_redis

    if not TASKS_REDIS_ENABLED:
        return None

    if _task_redis is None:
        _task_redis = redis.Redis.from_url(TASKS_REDIS_URL, decode_responses=True)

    return _task_redis


async def get_shared_task_record(task_id: str):
    """Read the shared task record when Redis-backed coordination is enabled."""
    try:
        client = await _get_task_redis_client()
        if client is None:
            return None

        raw_value = await client.get(_task_record_key(task_id))
    except Exception as exc:
        log.warning("Failed to read shared task record for %s: %s", task_id, exc)
        return None

    if raw_value is None:
        return None

    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        log.warning("Ignoring invalid shared task record for %s", task_id)
        return None


async def upsert_shared_task_record(
    task_id: str,
    *,
    state: SharedTaskState,
    metadata: dict[str, Any] | None = None,
):
    """Persist the task owner and state so other instances can reason about it."""
    try:
        client = await _get_task_redis_client()
        if client is None:
            return None

        existing_record = await get_shared_task_record(task_id) or {}
        task_metadata = task_metadata_by_id.get(task_id, {})
        merged_record = {
            **existing_record,
            **task_metadata,
            **(metadata or {}),
            "task_id": task_id,
            "owner_instance_id": existing_record.get(
                "owner_instance_id", TASK_INSTANCE_ID
            ),
            "state": state,
            "updated_at": time.time(),
        }

        ttl_seconds = (
            ACTIVE_TASK_TTL_SECONDS if state == "running" else RECENT_TASK_TTL_SECONDS
        )
        await client.set(
            _task_record_key(task_id),
            json.dumps(merged_record),
            ex=ttl_seconds,
        )
        return merged_record
    except Exception as exc:
        log.warning("Failed to update shared task record for %s: %s", task_id, exc)
        return None


async def publish_remote_stop_request(task_id: str, owner_instance_id: str):
    """Ask the owning instance to cancel a task that is not local to this process."""
    try:
        client = await _get_task_redis_client()
        if client is None:
            return False

        await client.publish(
            _task_cancel_channel(owner_instance_id),
            json.dumps({"task_id": task_id}),
        )
        return True
    except Exception as exc:
        log.warning(
            "Failed to publish remote stop request for %s to %s: %s",
            task_id,
            owner_instance_id,
            exc,
        )
        return False


async def wait_for_task_terminal_state(
    task_id: str,
    timeout_seconds: float = REMOTE_STOP_WAIT_SECONDS,
):
    """Wait briefly for a task to surface a terminal state locally or in Redis."""
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        task_state = get_recent_task_state(task_id)
        if task_state is not None:
            return task_state

        task_record = await get_shared_task_record(task_id)
        if task_record and task_record.get("state") in TERMINAL_TASK_STATES:
            return task_record["state"]

        await asyncio.sleep(REMOTE_STOP_POLL_INTERVAL_SECONDS)

    return None


def remember_task_state(task_id: str, state: TaskState):
    """
    Retain the terminal state for a recently finished task.
    """
    prune_recent_task_states()
    recent_task_states[task_id] = (state, time.monotonic())


def get_recent_task_state(task_id: str):
    """
    Retrieve the retained terminal state for a recently finished task.
    """
    prune_recent_task_states()
    task_state = recent_task_states.get(task_id)
    return task_state[0] if task_state else None


async def cleanup_task(task_id: str, task: asyncio.Task):
    """
    Remove the local task, cache its terminal state, and publish that state.
    """
    tasks.pop(task_id, None)
    task_state = _resolve_task_state(task)
    remember_task_state(task_id, task_state)

    metadata = task_metadata_by_id.pop(task_id, None)
    await upsert_shared_task_record(
        task_id,
        state=task_state,
        metadata=metadata,
    )
    return task_state


def _schedule_cleanup_task(task_id: str, task: asyncio.Task):
    try:
        task.get_loop().create_task(cleanup_task(task_id, task))
    except RuntimeError:
        log.debug(
            "Skipping task cleanup scheduling for %s because the loop is closed",
            task_id,
        )


async def create_task(coroutine, metadata: dict[str, Any] | None = None):
    """
    Register a task in shared state before it starts so stop requests can find it.
    """
    task_id = str(uuid4())  # Generate a unique ID for the task
    task_metadata = metadata or {}
    task_metadata_by_id[task_id] = task_metadata

    await upsert_shared_task_record(
        task_id,
        state="running",
        metadata=task_metadata,
    )

    task = asyncio.create_task(coroutine)  # Create the task

    # Add a done callback for cleanup
    task.add_done_callback(partial(_schedule_cleanup_task, task_id))

    tasks[task_id] = task
    return task_id, task


def get_task(task_id: str):
    """
    Retrieve a task by its task ID.
    """
    return tasks.get(task_id)


def list_tasks():
    """
    List all currently active task IDs.
    """
    return list(tasks.keys())


async def _cancel_local_task(task_id: str):
    """Cancel a task only if this instance still holds it locally."""
    task = tasks.get(task_id)

    if task is None or task.done():
        return False

    task.cancel()
    return True


async def _listen_for_task_stop_requests():
    """Consume this instance's stop channel and cancel matching local tasks."""
    channel_name = _task_cancel_channel(TASK_INSTANCE_ID)
    pubsub = None

    try:
        client = await _get_task_redis_client()
        if client is None:
            return

        pubsub = client.pubsub()
        await pubsub.subscribe(channel_name)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=REMOTE_STOP_POLL_INTERVAL_SECONDS,
            )

            if not message:
                await asyncio.sleep(REMOTE_STOP_POLL_INTERVAL_SECONDS)
                continue

            payload = message.get("data")
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")

            try:
                task_id = json.loads(payload).get("task_id")
            except Exception:
                log.warning("Ignoring malformed remote stop payload: %s", payload)
                continue

            if task_id:
                await _cancel_local_task(task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("Shared task stop listener failed: %s", exc)
    finally:
        if pubsub is None:
            return

        try:
            await pubsub.unsubscribe(channel_name)
        except Exception:
            pass

        close = getattr(pubsub, "aclose", pubsub.close)
        try:
            await close()
        except Exception:
            pass


async def start_task_cancellation_listener():
    """Ensure the per-instance Redis stop listener is running."""
    global _task_cancel_listener

    if not TASKS_REDIS_ENABLED:
        return

    # If a previous listener task has crashed or exited, allow a restart.
    # Without this check, a Redis blip that kills the listener would silently
    # disable all cross-pod stop routing for the lifetime of the process.
    if _task_cancel_listener is not None:
        if not _task_cancel_listener.done():
            return  # Still running — nothing to do.
        log.warning(
            "Task cancellation listener exited unexpectedly (state=%s). Restarting.",
            _task_cancel_listener._state,
        )

    _task_cancel_listener = asyncio.create_task(_listen_for_task_stop_requests())


async def stop_task_cancellation_listener():
    """Stop the per-instance listener and release the shared Redis client."""
    global _task_cancel_listener, _task_redis

    if _task_cancel_listener is not None:
        _task_cancel_listener.cancel()
        try:
            await _task_cancel_listener
        except asyncio.CancelledError:
            pass
        _task_cancel_listener = None

    if _task_redis is not None:
        try:
            await _task_redis.aclose()
        except Exception:
            pass
        _task_redis = None


async def stop_task(task_id: str):
    """
    Stop a task locally when possible or route the stop through shared ownership.
    """
    task = tasks.get(task_id)
    if task is None:
        recent_result = _get_recent_stop_result(task_id)
        if recent_result is not None:
            return recent_result

        task_record = await get_shared_task_record(task_id)
        if task_record is not None:
            task_record_result = _get_task_record_stop_result(task_id, task_record)
            if task_record_result is not None:
                return task_record_result

            owner_instance_id = task_record.get("owner_instance_id")
            if owner_instance_id and owner_instance_id != TASK_INSTANCE_ID:
                await publish_remote_stop_request(task_id, owner_instance_id)
                final_state = await wait_for_task_terminal_state(task_id)
                if final_state is not None:
                    return _build_stop_result(task_id, final_state, stop_requested=True)

                return {
                    "status": True,
                    "message": f"Stop requested for task {task_id} on {owner_instance_id}.",
                    "state": "cancelling",
                }

            # Shared record says this instance owns the task but it is not in
            # tasks[] — likely finished between Redis read and this call.
            # Wait briefly for the cleanup callback to populate local state.
            log.debug(
                "Task %s owned by this instance but not found locally; waiting for cleanup.",
                task_id,
            )
            final_state = await wait_for_task_terminal_state(
                task_id, timeout_seconds=0.5
            )
            if final_state is not None:
                return _build_stop_result(task_id, final_state)

        # Re-check the local terminal-state cache in case the task finished while
        # we were consulting shared state or a shared record was briefly stale.
        recent_result = _get_recent_stop_result(task_id)
        if recent_result is not None:
            return recent_result

        raise ValueError(f"Task with ID {task_id} not found.")

    if task.done():
        task_state = await cleanup_task(task_id, task)
        return _build_stop_result(task_id, task_state)

    task.cancel()  # Request task cancellation
    try:
        await task  # Wait for the task to handle the cancellation
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

    task_state = await cleanup_task(task_id, task)
    return _build_stop_result(task_id, task_state, stop_requested=True)
