from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from open_webui.env import (
    REDIS_URL,
    WEBSOCKET_MANAGER,
    WEBSOCKET_REDIS_URL,
)

from .backends import (
    InMemoryTaskHub,
    RedisTaskCoordinationBackend,
)
from .manager import TaskManager, TaskManagerSettings
from .models import TERMINAL_TASK_STATES, TaskLifecycleState

RECENT_TASK_TTL_SECONDS = 30
ACTIVE_TASK_TTL_SECONDS = 60 * 60
REMOTE_STOP_WAIT_SECONDS = 5
REMOTE_STOP_POLL_INTERVAL_SECONDS = 0.1

TASK_COORDINATION_DISTRIBUTED = WEBSOCKET_MANAGER == "redis"
TASK_COORDINATION_URL = (
    WEBSOCKET_REDIS_URL if WEBSOCKET_MANAGER == "redis" else REDIS_URL
)
TASK_RECORD_KEY_PREFIX = "open-webui:tasks"
TASK_INSTANCE_CHANNEL_PREFIX = "open-webui:tasks:instance"
TASK_INSTANCE_ID = (
    f"{os.environ.get('HOSTNAME') or 'local'}:{os.getpid()}:{uuid4().hex[:8]}"
)

TaskState = str
SharedTaskState = str

_default_task_manager: TaskManager | None = None


def create_default_task_manager(
    *,
    instance_id: str = TASK_INSTANCE_ID,
    recent_task_ttl_seconds: int = RECENT_TASK_TTL_SECONDS,
    active_task_ttl_seconds: int = ACTIVE_TASK_TTL_SECONDS,
    remote_stop_wait_seconds: float = REMOTE_STOP_WAIT_SECONDS,
) -> TaskManager:
    settings = TaskManagerSettings(
        instance_id=instance_id,
        recent_task_ttl_seconds=recent_task_ttl_seconds,
        remote_stop_wait_seconds=remote_stop_wait_seconds,
    )

    if TASK_COORDINATION_DISTRIBUTED:
        backend = RedisTaskCoordinationBackend(
            redis_url=TASK_COORDINATION_URL,
            record_key_prefix=TASK_RECORD_KEY_PREFIX,
            instance_channel_prefix=TASK_INSTANCE_CHANNEL_PREFIX,
            active_task_ttl_seconds=active_task_ttl_seconds,
            recent_task_ttl_seconds=recent_task_ttl_seconds,
            listener_poll_interval_seconds=REMOTE_STOP_POLL_INTERVAL_SECONDS,
        )
    else:
        backend = InMemoryTaskHub(
            active_task_ttl_seconds=active_task_ttl_seconds,
            recent_task_ttl_seconds=recent_task_ttl_seconds,
        ).create_backend()

    return TaskManager(backend=backend, settings=settings)


def get_task_manager() -> TaskManager:
    global _default_task_manager

    if _default_task_manager is None:
        _default_task_manager = create_default_task_manager()

    return _default_task_manager


def set_task_manager(task_manager: TaskManager) -> TaskManager:
    global _default_task_manager

    _default_task_manager = task_manager
    return task_manager


def clear_task_manager() -> None:
    global _default_task_manager

    _default_task_manager = None


def reset_task_manager(**kwargs: Any) -> TaskManager:
    task_manager = create_default_task_manager(**kwargs)
    return set_task_manager(task_manager)


__all__ = [
    "ACTIVE_TASK_TTL_SECONDS",
    "RECENT_TASK_TTL_SECONDS",
    "REMOTE_STOP_POLL_INTERVAL_SECONDS",
    "REMOTE_STOP_WAIT_SECONDS",
    "TASK_COORDINATION_DISTRIBUTED",
    "TASK_COORDINATION_URL",
    "TASK_RECORD_KEY_PREFIX",
    "TASK_INSTANCE_CHANNEL_PREFIX",
    "TASK_INSTANCE_ID",
    "TERMINAL_TASK_STATES",
    "TaskLifecycleState",
    "TaskManager",
    "TaskManagerSettings",
    "InMemoryTaskHub",
    "create_default_task_manager",
    "get_task_manager",
    "set_task_manager",
    "clear_task_manager",
    "reset_task_manager",
]
