import asyncio
import logging
from typing import Any, Coroutine, Optional
from uuid import uuid4

from open_webui.tasks.streams.command_bus.base import CommandBus
from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.executor import StreamExecutor
from open_webui.tasks.streams.models import StopCompletedEvent, StopStreamCommand
from open_webui.tasks.streams.registry import StreamRegistry

log = logging.getLogger(__name__)


class StreamManager:
    def __init__(
        self,
        registry: Optional[StreamRegistry] = None,
        executor: Optional[StreamExecutor] = None,
        bus: Optional[CommandBus] = None,
        instance_id: Optional[str] = None,
        remote_stop_timeout: float = 5.0,
    ) -> None:
        self._registry = registry or StreamRegistry()
        self._executor = executor or StreamExecutor(self._registry)
        self._bus = bus or LocalCommandBus()
        self._instance_id = instance_id or str(uuid4())
        self._remote_stop_timeout = remote_stop_timeout

        self._listener_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None
        self._pending_stops: dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        await self._start_listener()

    async def close(self) -> None:
        await self._stop_listener()

    async def _start_listener(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return
        self._queue = await self._bus.subscribe()
        self._listener_task = asyncio.create_task(
            self._listen_loop(),
            name="stream-task-listener",
        )

    async def _stop_listener(self) -> None:
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
        """Stop a stream. Handles both local and remote (cross-pod) tasks."""
        record = await self._registry.get(stream_id)
        if record is not None:
            # Task is local: call the executor directly, no bus round-trip needed.
            terminal_state = await self._executor.stop(stream_id)
        else:
            # Not in local registry: publish to bus and wait for the owning pod to confirm.
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending_stops[stream_id] = future

            try:
                log.debug("Publishing stop command for stream '%s'", stream_id)
                await self._bus.publish(
                    StopStreamCommand(
                        stream_id=stream_id,
                        source_instance_id=self._instance_id,
                    )
                )
                terminal_state = await asyncio.wait_for(
                    future, timeout=self._remote_stop_timeout
                )
            except asyncio.TimeoutError:
                log.debug(
                    "Timed out waiting for stop confirmation for stream '%s'",
                    stream_id,
                )
                raise ValueError(f"Task with ID {stream_id} not found.")
            except Exception as exc:
                log.error(
                    "Failed to publish stop command for '%s': %s",
                    stream_id,
                    exc,
                )
                raise
            finally:
                self._pending_stops.pop(stream_id, None)

        msg = (
            f"Task {stream_id} already {terminal_state}."
            if terminal_state in ("completed", "failed")
            else f"Task {stream_id} successfully stopped."
        )
        return {"status": True, "message": msg, "state": terminal_state}

    async def list(self) -> list[dict[str, Any]]:
        return await self._registry.list()

    async def get(self, stream_id: str) -> Optional[dict[str, Any]]:
        record = await self._registry.get(stream_id)
        return None if record is None else record.public()

    async def _listen_loop(self) -> None:
        if self._queue is None:
            log.warning("Stream listener started without a queue")
            return

        while True:
            message = await self._queue.get()

            if isinstance(message, StopStreamCommand):
                if message.source_instance_id == self._instance_id:
                    continue  # self-echo: we published this, skip it

                try:
                    log.debug(
                        "Received stop command for stream '%s'", message.stream_id
                    )
                    terminal_state = await self._executor.stop(message.stream_id)
                    await self._bus.publish(
                        StopCompletedEvent(
                            stream_id=message.stream_id,
                            terminal_state=terminal_state,
                        )
                    )
                except ValueError as exc:
                    log.debug(
                        "Ignoring stop command for unknown stream '%s': %s",
                        message.stream_id,
                        exc,
                    )
                except Exception as exc:
                    log.warning(
                        "Failed to process stop command for '%s': %s",
                        message.stream_id,
                        exc,
                    )

            elif isinstance(message, StopCompletedEvent):
                future = self._pending_stops.get(message.stream_id)
                if future and not future.done():
                    log.debug(
                        "Received stop completion for stream '%s': %s",
                        message.stream_id,
                        message.terminal_state,
                    )
                    future.set_result(message.terminal_state)
