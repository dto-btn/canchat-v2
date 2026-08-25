from __future__ import annotations

from open_webui.env import (
    STREAM_INSTANCE_ID,
    TASK_COORDINATION_BACKEND,
    TASK_COORDINATION_REDIS_CONNECT_TIMEOUT,
    TASK_COORDINATION_REDIS_HEALTH_CHECK_INTERVAL,
    TASK_COORDINATION_URL,
)

from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.command_bus.redis import RedisCommandBus
from open_webui.tasks.streams.manager import StreamManager


def create_stream_manager() -> StreamManager:
    if TASK_COORDINATION_BACKEND == "redis" and TASK_COORDINATION_URL:
        bus = RedisCommandBus(
            redis_url=TASK_COORDINATION_URL,
            socket_connect_timeout=TASK_COORDINATION_REDIS_CONNECT_TIMEOUT,
            health_check_interval=TASK_COORDINATION_REDIS_HEALTH_CHECK_INTERVAL,
        )
    else:
        bus = LocalCommandBus()

    return StreamManager(bus=bus, instance_id=STREAM_INSTANCE_ID)
