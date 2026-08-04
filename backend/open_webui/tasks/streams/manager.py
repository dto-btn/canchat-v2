import asyncio
import logging
from typing import Any, Coroutine, Optional
from uuid import uuid4

from open_webui.tasks.streams.command_bus.base import CommandBus
from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.executor import StreamExecutor
from open_webui.tasks.streams.models import StopStreamCommand
from open_webui.tasks.streams.registry import StreamRegistry

log = logging.getLogger(__name__)


class StreamManager:
    def __init__(
        self,
        registry: Optional[StreamRegistry] = None,
        executor: Optional[StreamExecutor] = None,
        bus: Optional[CommandBus] = None,
        instance_id: str = "local-" + uuid4().hex[:8],
    ) -> None:
        self._registry = registry or StreamRegistry()
        self._executor = executor or StreamExecutor(self._registry)
        self._bus = bus or LocalCommandBus()
        self._instance_id = instance_id

        self._listener_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None

    async def start(self) -> None:
        await self.start_listener()

    async def close(self) -> None:
        await self.stop_listener()

    async def start_listener(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return
        self._queue = await self._bus.subscribe()
        self._listener_task = asyncio.create_task(
            self._listen_loop(),
            name="stream-task-listener",
        )

    async def stop_listener(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._queue:
            await self._bus.unsubscribe(self._queue)
            self._queue = None

    async def create(
        self, coroutine: Coroutine, metadata: Optional[dict[str, Any]] = None
    ) -> tuple[str, asyncio.Task]:
        stream_id = str(uuid4())
        await self._registry.create(stream_id, coroutine, metadata)
        task = await self._executor.execute(stream_id)
        return stream_id, task

    async def stop(self, stream_id: str) -> dict[str, Any]:
        rec = await self._registry.get(stream_id)
        if rec is None:
            raise ValueError(f"Task with ID {stream_id} not found.")

        try:
            stopped = await self._executor.stop(stream_id)
            if not stopped:
                raise ValueError(f"Task with ID {stream_id} not found.")

            return {
                "status": True,
                "message": f"Task {stream_id} successfully stopped.",
            }
        except ValueError as exc:
            return {"status": False, "message": str(exc)}

    async def stop_request(
        self,
        stream_id: str,
        target_instance_id: str | None = None,
    ) -> None:
        try:
            cmd = StopStreamCommand(
                stream_id=stream_id,
                target_instance_id=target_instance_id,
            )
            await self._bus.publish(cmd)
        except Exception as exc:
            log.error("Failed to publish stop command for '%s': %s", stream_id, exc)

    async def list(self) -> list[dict[str, Any]]:
        return await self._registry.list()

    async def get(self, stream_id: str) -> Optional[dict[str, Any]]:
        rec = await self._registry.get(stream_id)
        return None if rec is None else rec.public()

    async def _listen_loop(self) -> None:
        if self._queue is None:
            log.warning("Stream listener started without a queue")
            return

        while True:
            cmd = await self._queue.get()
            if isinstance(cmd, StopStreamCommand):
                if (
                    cmd.target_instance_id
                    and cmd.target_instance_id != self._instance_id
                ):
                    continue
                try:
                    log.debug(f"Received stop command for stream '{cmd.stream_id}'")
                    await self.stop(cmd.stream_id)
                except ValueError as exc:
                    log.debug(
                        "Ignoring stop command for unknown stream '%s': %s",
                        cmd.stream_id,
                        exc,
                    )
                except Exception as exc:
                    log.warning(
                        "Failed to process stop command for '%s': %s",
                        cmd.stream_id,
                        exc,
                    )
