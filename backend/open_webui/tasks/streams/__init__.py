from __future__ import annotations

import os

from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.command_bus.redis import RedisCommandBus
from open_webui.tasks.streams.manager import StreamManager


def create_stream_manager() -> StreamManager:
    # TODO: to update and add the env variables to env file
    instance_id = os.getenv("TASK_INSTANCE_ID", "local")
    coordination_url = os.getenv("TASK_COORDINATION_URL", "")
    distributed = os.getenv("TASK_COORDINATION_DISTRIBUTED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if distributed and coordination_url:
        bus = RedisCommandBus(redis_url=coordination_url)
    else:
        bus = LocalCommandBus()

    return StreamManager(bus=bus, instance_id=instance_id)
