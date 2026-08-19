import asyncio
import logging

from open_webui.tasks.streams.models import StreamStatus
from open_webui.tasks.streams.registry import StreamRegistry

log = logging.getLogger(__name__)


class StreamExecutor:
    def __init__(self, registry: StreamRegistry) -> None:
        self._registry = registry

    async def execute(self, stream_id: str) -> asyncio.Task:
        record = await self._registry.get(stream_id)
        if record is None:
            raise ValueError(f"Task with ID {stream_id} not found.")

        task = asyncio.create_task(record.coroutine, name=f"stream:{stream_id}")
        if task is None:
            raise ValueError(
                f"Failed to create asyncio task for stream ID {stream_id}."
            )

        def _done_callback(done_task: asyncio.Task):
            asyncio.create_task(self._finalize(stream_id, done_task))

        task.add_done_callback(_done_callback)
        await self._registry.update(stream_id, task=task, status=StreamStatus.RUNNING)
        return task

    async def stop(self, stream_id: str) -> str:
        """Cancel the task and return its terminal state."""
        record = await self._registry.get(stream_id)
        if record is None:
            raise ValueError(f"Task with ID {stream_id} not found.")

        if record.task is None:
            raise ValueError(
                f"Task with ID {stream_id} has no associated asyncio task."
            )

        if record.task.done():
            state = _terminal_state(record.task)
            await self._registry.remove(stream_id)
            log.debug("Stream '%s' was already complete: %s", stream_id, state)
            return state

        await self._registry.mark_cancelling(stream_id)
        log.debug("Stopping stream '%s' by cancelling its task", stream_id)
        record.task.cancel()
        try:
            await record.task
        except asyncio.CancelledError:
            log.debug("Stream '%s' cancellation observed", stream_id)
            pass
        await self._registry.remove(stream_id)
        # Always "cancelled": we initiated the stop even if the task swallowed the error.
        return "cancelled"

    async def _finalize(self, stream_id: str, task: asyncio.Task) -> None:
        try:
            if task.cancelled():
                log.debug(
                    "Stream '%s' was cancelled and removed from registry", stream_id
                )
                await self._registry.remove(stream_id)
                return

            exc = task.exception()
            if exc is not None:
                log.error(
                    "Stream '%s' failed (%s) and was removed from registry",
                    stream_id,
                    exc,
                )
                await self._registry.remove(stream_id)
                return

            log.debug(
                "Stream '%s' completed successfully and was removed from registry",
                stream_id,
            )
            await self._registry.remove(stream_id)
        except KeyError:
            log.debug("Stream '%s' already removed before finalize", stream_id)


def _terminal_state(task: asyncio.Task) -> str:
    if task.cancelled():
        return "cancelled"
    if task.exception() is not None:
        return "failed"
    return "completed"
