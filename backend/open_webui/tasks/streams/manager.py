import asyncio
import logging
from typing import Any, Coroutine, Optional
from uuid import uuid4

from open_webui.tasks.streams.command_bus.base import CommandBus, StreamBusMessage
from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.executor import StreamExecutor
from open_webui.tasks.streams.models import (
    StopCompletedEvent,
    StopErrorCode,
    StopStreamCommand,
    StreamRecord,
    TerminalState,
)
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
        self._queue: Optional[asyncio.Queue[StreamBusMessage]] = None
        self._pending_stops: dict[str, asyncio.Future[StopCompletedEvent]] = {}

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

        for future in self._pending_stops.values():
            if not future.done():
                future.cancel()
        self._pending_stops.clear()

    async def create(
        self, coroutine: Coroutine, metadata: Optional[dict[str, Any]] = None
    ) -> tuple[str, asyncio.Task]:
        stream_id = str(uuid4())
        await self._registry.create(stream_id, coroutine, metadata)
        task = await self._executor.execute(stream_id)
        return stream_id, task

    async def stop(
        self,
        stream_id: str,
        requester_user_id: str,
        requester_is_admin: bool = False,
    ) -> dict[str, Any]:
        """Stop a stream. Handles both local and remote (cross-pod) tasks."""
        record = await self._registry.get(stream_id)
        if record is not None:
            # Task is local: call the executor directly, no bus round-trip needed.
            if not self._can_access_record(
                record, requester_user_id, requester_is_admin
            ):
                raise PermissionError(
                    f"Task with ID {stream_id} does not belong to the authenticated user."
                )
            terminal_state = await self._executor.stop(stream_id)
        else:
            # The local registry is authoritative for local ownership. A missing record
            # may belong to another pod, so ask the owner to authorize and stop it.
            loop = asyncio.get_running_loop()
            request_id = str(uuid4())
            future: asyncio.Future[StopCompletedEvent] = loop.create_future()
            self._pending_stops[request_id] = future

            try:
                log.debug("Publishing stop command for stream '%s'", stream_id)
                await self._bus.publish(
                    StopStreamCommand(
                        stream_id=stream_id,
                        request_id=request_id,
                        source_instance_id=self._instance_id,
                        requester_user_id=requester_user_id,
                        requester_is_admin=requester_is_admin,
                    )
                )
                result = await asyncio.wait_for(
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
                self._pending_stops.pop(request_id, None)
                if not future.done():
                    future.cancel()

            terminal_state = self._resolve_remote_stop_result(stream_id, result)

        msg = (
            f"Task {stream_id} already {terminal_state}."
            if terminal_state in ("completed", "failed")
            else f"Task {stream_id} successfully stopped."
        )
        return {"status": True, "message": msg, "state": terminal_state}

    async def list(
        self,
        requester_user_id: str,
        requester_is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        records = await self._registry.list()
        return [
            record.summary()
            for record in records
            if self._can_access_record(record, requester_user_id, requester_is_admin)
        ]

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
                    record = await self._registry.get(message.stream_id)
                    if record is None:
                        log.debug(
                            "Ignoring stop command for non-local stream '%s'",
                            message.stream_id,
                        )
                        continue

                    if not self._can_access_record(
                        record,
                        message.requester_user_id,
                        message.requester_is_admin,
                    ):
                        log.warning(
                            "Rejecting stop command for stream '%s' from user '%s'",
                            message.stream_id,
                            message.requester_user_id,
                        )
                        await self._publish_remote_stop_result(
                            message,
                            error_code="forbidden",
                        )
                        continue

                    log.debug(
                        "Received stop command for stream '%s'", message.stream_id
                    )
                    # The record can finish after the ownership check. executor.stop()
                    # treats that race as not found and sends a terminal response.
                    terminal_state = await self._executor.stop(message.stream_id)
                    await self._publish_remote_stop_result(
                        message,
                        terminal_state=terminal_state,
                    )
                except ValueError as exc:
                    log.debug(
                        "Stop target disappeared before cancellation for '%s': %s",
                        message.stream_id,
                        exc,
                    )
                    await self._publish_remote_stop_result(
                        message,
                        error_code="not_found",
                    )
                except Exception as exc:
                    log.warning(
                        "Failed to process stop command for '%s': %s",
                        message.stream_id,
                        exc,
                    )
                    await self._publish_remote_stop_result(
                        message,
                        terminal_state="failed",
                    )

            elif isinstance(message, StopCompletedEvent):
                future = self._pending_stops.get(message.request_id)
                if future and not future.done():
                    log.debug(
                        "Received stop completion for stream '%s': %s",
                        message.stream_id,
                        message.terminal_state or message.error_code,
                    )
                    future.set_result(message)

    def _can_access_record(
        self,
        record: StreamRecord,
        requester_user_id: str,
        requester_is_admin: bool,
    ) -> bool:
        if requester_is_admin:
            return True
        owner_user_id = record.metadata.get("user_id")
        return owner_user_id is not None and owner_user_id == requester_user_id

    def _resolve_remote_stop_result(
        self,
        stream_id: str,
        result: StopCompletedEvent,
    ) -> TerminalState:
        if result.error_code == "not_found":
            raise ValueError(f"Task with ID {stream_id} not found.")
        if result.error_code == "forbidden":
            raise PermissionError(
                f"Task with ID {stream_id} does not belong to the authenticated user."
            )
        if result.terminal_state is None:
            raise RuntimeError(
                f"Stop completion for task {stream_id} did not include a terminal state."
            )
        return result.terminal_state

    async def _publish_remote_stop_result(
        self,
        message: StopStreamCommand,
        *,
        terminal_state: Optional[TerminalState] = None,
        error_code: Optional[StopErrorCode] = None,
    ) -> None:
        await self._bus.publish(
            StopCompletedEvent(
                stream_id=message.stream_id,
                request_id=message.request_id,
                terminal_state=terminal_state,
                error_code=error_code,
            )
        )
