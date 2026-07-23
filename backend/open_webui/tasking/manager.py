from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import uuid4

from .backends import TaskCoordinationBackend
from .models import (
    RemoteStopRequest,
    TaskHandle,
    TaskLifecycleState,
    TaskRecord,
    get_task_state,
    is_terminal_state,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskManagerSettings:
    instance_id: str
    recent_task_ttl_seconds: int = 30
    remote_stop_wait_seconds: float = 5.0


@dataclass(slots=True)
class _RemoteStopWaiter:
    task_id: str
    future: asyncio.Future[TaskLifecycleState]
    last_state: TaskLifecycleState | None = None


class TaskManager:
    def __init__(
        self,
        *,
        backend: TaskCoordinationBackend,
        settings: TaskManagerSettings,
    ):
        self.backend = backend
        self.settings = settings

        self._started = False
        self._handles: dict[str, TaskHandle] = {}
        self._recent_terminal_records: dict[str, tuple[TaskRecord, float]] = {}
        self._pending_stop_waiters: dict[str, _RemoteStopWaiter] = {}

    @property
    def instance_id(self) -> str:
        return self.settings.instance_id

    async def start(self) -> None:
        if self._started:
            return

        await self.backend.start(self.instance_id, self._handle_backend_message)
        self._started = True

    async def close(self) -> None:
        if not self._started:
            return

        for waiter in self._pending_stop_waiters.values():
            if not waiter.future.done():
                waiter.future.cancel()

        self._pending_stop_waiters.clear()
        await self.backend.close()
        self._started = False

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    async def create(
        self,
        coroutine,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, asyncio.Task]:
        await self._ensure_started()

        task_id = str(uuid4())
        loop = asyncio.get_running_loop()
        handle = TaskHandle(
            record=TaskRecord(
                task_id=task_id,
                owner_instance_id=self.instance_id,
                state=TaskLifecycleState.RUNNING,
                metadata=dict(metadata or {}),
            ),
            terminal_future=loop.create_future(),
        )
        self._handles[task_id] = handle

        await self.backend.save_record(handle.record)

        task = asyncio.create_task(coroutine)
        handle.bind_task(task)
        # Register cleanup before returning so it fires even if the task completes
        # synchronously on the next event loop tick before the caller awaits it.
        task.add_done_callback(partial(self._schedule_cleanup_task, task_id))
        return task_id, task

    def get_task(self, task_id: str) -> asyncio.Task | None:
        handle = self._handles.get(task_id)
        return handle.task if handle is not None else None

    def list(self) -> list[str]:
        return list(self._handles.keys())

    async def stop(self, task_id: str) -> dict[str, Any]:
        await self._ensure_started()

        # 1. Live local handle — handle it in-process.
        handle = self._handles.get(task_id)
        if handle is not None:
            return await self._stop_local_handle(handle)

        # 2. Recent terminal cache — fast path, no backend round-trip needed.
        recent_result = self._get_recent_stop_result(task_id)
        if recent_result is not None:
            return recent_result

        # 3. Shared backend record — determine ownership and dispatch.
        record = await self.load_record(task_id, include_recent_cache=False)
        if record is not None:
            # 3a. Task is already terminal in the shared store.
            record_result = self._get_record_stop_result(task_id, record)
            if record_result is not None:
                return record_result

            # 3b. Owned by another instance — relay via message.
            if record.owner_instance_id != self.instance_id:
                return await self._stop_remote_task(task_id, record.owner_instance_id)

            # 3c. Owned locally but handle is gone — likely a cleanup race; reconcile.
            log.debug(
                "Task %s owned by this instance but not found locally; reconciling.",
                task_id,
            )
            final_state = await self.reconcile(
                task_id, record=record, timeout_seconds=0.5
            )
            if final_state is not None:
                return self._build_stop_result(task_id, final_state)

        # 4. Final cache check: catch terminal events that arrived while we were loading
        #    the backend record or waiting during reconcile above.
        recent_result = self._get_recent_stop_result(task_id)
        if recent_result is not None:
            return recent_result

        raise ValueError(f"Task with ID {task_id} not found.")

    async def cleanup(self, task_id: str, task: asyncio.Task) -> str:
        handle = self._handles.get(task_id)
        if handle is None:
            # Handle not found: either a duplicate cleanup call or the handle was never
            # registered. Return any cached terminal state, or derive one from the asyncio
            # task and persist a minimal record to keep the shared store consistent.
            recent_state = self.get_recent_task_state(task_id)
            if recent_state is not None:
                return recent_state

            terminal_state = self._resolve_terminal_state(None, task)
            record = TaskRecord(
                task_id=task_id,
                owner_instance_id=self.instance_id,
                state=terminal_state,
            )
            self._remember_terminal_record(record)
            await self.backend.save_record(record)
            return terminal_state.value

        # Coroutine completed before bind_task() was called in create(); bind it now.
        if handle.task is None:
            handle.bind_task(task)

        # Idempotency guard: cleanup was already run for this handle, just evict it.
        if handle.record.is_terminal and handle.terminal_future.done():
            self._remember_terminal_record(handle.record)
            self._handles.pop(task_id, None)
            return handle.record.state.value

        terminal_state = self._resolve_terminal_state(handle, task)
        handle.mark_terminal(terminal_state)
        self._remember_terminal_record(handle.record)
        await self.backend.save_record(handle.record)
        await self._notify_remote_stop_requests(handle)
        self._handles.pop(task_id, None)
        return terminal_state.value

    def _schedule_cleanup_task(self, task_id: str, task: asyncio.Task) -> None:
        try:
            # Use task.get_loop() rather than asyncio.get_running_loop() because done
            # callbacks may fire after the event loop has stopped (e.g. during test teardown).
            task.get_loop().create_task(self.cleanup(task_id, task))
        except RuntimeError:
            log.debug(
                "Skipping task cleanup scheduling for %s because the loop is closed",
                task_id,
            )

    # Determine the current terminal state of a task using the cheapest available source.
    # Checks in order: live handle future → local recency cache → shared backend record.
    # Returns None if the task is still running or unknown within timeout_seconds.
    async def reconcile(
        self,
        task_id: str,
        *,
        record: TaskRecord | None = None,
        timeout_seconds: float = 0.5,
    ) -> TaskLifecycleState | None:
        handle = self._handles.get(task_id)
        if handle is not None:
            task = handle.task
            # Late arriving task cleanup
            if task is not None and task.done() and not handle.terminal_future.done():
                await self.cleanup(task_id, task)

            if handle.terminal_future.done():
                return handle.record.state

            try:
                # Shield so a TimeoutError doesn't cancel the underlying future.
                return await asyncio.wait_for(
                    asyncio.shield(handle.terminal_future),
                    timeout_seconds,
                )
            except asyncio.TimeoutError:
                return None

        recent_record = self._get_recent_terminal_record(task_id)
        if recent_record is not None:
            return recent_record.state

        if record is None:
            record = await self.backend.load_record(task_id)

        if record is None:
            return None

        if record.is_terminal:
            self._remember_terminal_record(record)
            return record.state

        return None

    async def wait_for_terminal_state(
        self,
        task_id: str,
        timeout_seconds: float,
    ) -> str | None:
        # Fast path: reconcile waits on the live handle future and checks local cache.
        final_state = await self.reconcile(
            task_id,
            timeout_seconds=timeout_seconds,
        )
        if final_state is not None:
            return final_state.value

        # Slow path: no live handle resolved within timeout; consult the shared backend.
        record = await self.load_record(task_id, include_recent_cache=False)
        if record is not None and record.is_terminal:
            self._remember_terminal_record(record)
            return record.state.value

        return self.get_recent_task_state(task_id)

    async def load_record(
        self,
        task_id: str,
        *,
        include_recent_cache: bool = True,
    ) -> TaskRecord | None:
        handle = self._handles.get(task_id)
        if handle is not None:
            return handle.record.clone()

        if include_recent_cache:
            recent_record = self._get_recent_terminal_record(task_id)
            if recent_record is not None:
                return recent_record  # already a copy; _get_recent_terminal_record clones on retrieval

        return await self.backend.load_record(task_id)

    async def load_record_dict(self, task_id: str) -> dict[str, Any] | None:
        record = await self.load_record(task_id)
        return None if record is None else record.to_payload()

    async def upsert_record(
        self,
        task_id: str,
        *,
        state: TaskLifecycleState | str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        next_state = get_task_state(state)
        handle = self._handles.get(task_id)
        if handle is not None:
            # Live handle: mutate the authoritative in-process record using state machine
            # methods to keep transitions consistent, then persist to the shared store.
            if metadata:
                handle.record.metadata.update(metadata)

            if next_state == TaskLifecycleState.CANCELLING:
                handle.request_stop()
            elif is_terminal_state(next_state):
                handle.mark_terminal(next_state)
            else:
                handle.record.state = next_state
                handle.record.touch()

            await self.backend.save_record(handle.record)
            return handle.record.to_payload()

        # No live handle: operate on the shared backend record directly.
        # Used for cross-instance state propagation or tasks whose handle was already evicted.
        record = await self.backend.load_record(task_id)
        if record is None:
            record = TaskRecord(
                task_id=task_id,
                owner_instance_id=self.instance_id,
                state=next_state,
                metadata=dict(metadata or {}),
            )
        else:
            if metadata:
                record.metadata.update(metadata)

            if next_state == TaskLifecycleState.CANCELLING:
                record.request_stop()
            elif is_terminal_state(next_state):
                record.mark_terminal(next_state)
            else:
                record.state = next_state
                record.touch()

        await self.backend.save_record(record)
        if record.is_terminal:
            self._remember_terminal_record(record)
        return record.to_payload()

    async def request_remote_stop(
        self,
        task_id: str,
        owner_instance_id: str,
        *,
        request_id: str | None = None,
    ) -> bool:
        await self._ensure_started()
        stop_request_id = request_id or uuid4().hex
        return await self.backend.send_message(
            owner_instance_id,
            {
                "type": "stop-request",
                "task_id": task_id,
                "request_id": stop_request_id,
                "requester_instance_id": self.instance_id,
                "requested_at": time.time(),
            },
        )

    def remember_task_state(
        self,
        task_id: str,
        state: TaskLifecycleState | str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        terminal_state = get_task_state(state)
        record = TaskRecord(
            task_id=task_id,
            owner_instance_id=self.instance_id,
            state=terminal_state,
            metadata=dict(metadata or {}),
        )
        self._remember_terminal_record(record)

    def get_recent_task_state(self, task_id: str) -> str | None:
        recent_record = self._get_recent_terminal_record(task_id)
        return None if recent_record is None else recent_record.state.value

    def prune_recent_terminal_records(self) -> None:
        now = time.monotonic()
        expired_task_ids = [
            task_id
            for task_id, (_, finished_at) in self._recent_terminal_records.items()
            if now - finished_at > self.settings.recent_task_ttl_seconds
        ]

        for task_id in expired_task_ids:
            self._recent_terminal_records.pop(task_id, None)

    def _get_recent_terminal_record(self, task_id: str) -> TaskRecord | None:
        self.prune_recent_terminal_records()
        recent_record = self._recent_terminal_records.get(task_id)
        return None if recent_record is None else recent_record[0].clone()

    def _remember_terminal_record(self, record: TaskRecord) -> None:
        self.prune_recent_terminal_records()
        # Store directly; callers always pass an owned or freshly loaded record, and
        # _get_recent_terminal_record clones on retrieval to protect the stored copy.
        self._recent_terminal_records[record.task_id] = (record, time.monotonic())

    async def _stop_local_handle(self, handle: TaskHandle) -> dict[str, Any]:
        task = handle.task
        if task is None:
            final_state = await self.reconcile(handle.task_id, timeout_seconds=0.5)
            if final_state is not None:
                return self._build_stop_result(handle.task_id, final_state)
            raise ValueError(f"Task with ID {handle.task_id} not found.")

        if task.done():
            task_state = await self.cleanup(handle.task_id, task)
            return self._build_stop_result(handle.task_id, task_state)

        handle.request_stop()
        await self.backend.save_record(handle.record)

        cancel_sent = task.cancel()
        handle.note_cancel_signal_sent(cancel_sent)

        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

        if not handle.terminal_future.done():
            await self.cleanup(handle.task_id, task)

        # Shield: cleanup resolved the future while we awaited the task above;
        # shielding prevents a spurious cancellation from an outer timeout.
        final_state = await asyncio.shield(handle.terminal_future)
        return self._build_stop_result(handle.task_id, final_state, stop_requested=True)

    async def _stop_remote_task(
        self,
        task_id: str,
        owner_instance_id: str,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        request_id = uuid4().hex
        # Register the waiter before sending so a near-instant stop-event reply
        # cannot arrive before _pending_stop_waiters is populated.
        waiter = _RemoteStopWaiter(task_id=task_id, future=loop.create_future())
        self._pending_stop_waiters[request_id] = waiter

        sent = await self.backend.send_message(
            owner_instance_id,
            {
                "type": "stop-request",
                "task_id": task_id,
                "request_id": request_id,
                "requester_instance_id": self.instance_id,
                "requested_at": time.time(),
            },
        )

        if not sent:
            # Message delivery failed (owner channel unreachable). Optimistically report
            # CANCELLING; the stop may still take effect on the owner's next startup.
            self._pending_stop_waiters.pop(request_id, None)
            return {
                "status": True,
                "message": f"Stop requested for task {task_id} on {owner_instance_id}.",
                "state": "cancelling",
            }

        try:
            # Normal path: block until the owner sends a terminal stop-event.
            final_state = await asyncio.wait_for(
                asyncio.shield(waiter.future),
                self.settings.remote_stop_wait_seconds,
            )
            return self._build_stop_result(task_id, final_state, stop_requested=True)
        except asyncio.TimeoutError:
            # Owner didn't ack in time. Try local cache first (stop-events update it),
            # then fall back to the shared record, then use last_state as best effort.
            recent_state = self.get_recent_task_state(task_id)
            if recent_state is not None:
                return self._build_stop_result(
                    task_id, recent_state, stop_requested=True
                )

            record = await self.backend.load_record(task_id)
            if record is not None and record.is_terminal:
                self._remember_terminal_record(record)
                return self._build_stop_result(
                    task_id, record.state, stop_requested=True
                )

            # No confirmed terminal state; report the most recent intermediate state seen
            # via any stop-event during the wait window, or default to "cancelling".
            return {
                "status": True,
                "message": f"Stop requested for task {task_id} on {owner_instance_id}.",
                "state": (
                    waiter.last_state.value
                    if waiter.last_state is not None
                    else "cancelling"
                ),
            }
        finally:
            self._pending_stop_waiters.pop(request_id, None)

    def _resolve_terminal_state(
        self,
        handle: TaskHandle | None,
        task: asyncio.Task,
    ) -> TaskLifecycleState:
        if task.cancelled():
            return TaskLifecycleState.CANCELLED

        try:
            task_exception = task.exception()
        except asyncio.CancelledError:
            # task.exception() itself raises CancelledError for tasks cancelled via a
            # shielded future — treat it the same as task.cancelled() returning True.
            return TaskLifecycleState.CANCELLED

        if task_exception is not None:
            return TaskLifecycleState.FAILED

        # If a cancel signal was delivered but the coroutine swallowed CancelledError
        # and returned normally, honour the intent and classify the outcome as CANCELLED.
        if handle is not None and handle.cancel_signal_sent:
            return TaskLifecycleState.CANCELLED

        return TaskLifecycleState.COMPLETED

    def _build_stop_result(
        self,
        task_id: str,
        state: TaskLifecycleState | str,
        *,
        stop_requested: bool = False,
    ) -> dict[str, Any]:
        normalized_state = get_task_state(state)
        if normalized_state == TaskLifecycleState.FAILED:
            return {
                "status": False,
                "message": f"Task {task_id} failed before it could be stopped.",
                "state": "failed",
            }

        if normalized_state == TaskLifecycleState.CANCELLED:
            return {
                "status": True,
                "message": (
                    f"Task {task_id} successfully stopped."
                    if stop_requested
                    else f"Task {task_id} already cancelled."
                ),
                "state": "cancelled",
            }

        if normalized_state == TaskLifecycleState.CANCELLING:
            return {
                "status": True,
                "message": f"Stop requested for task {task_id}.",
                "state": "cancelling",
            }

        return {
            "status": True,
            "message": f"Task {task_id} already completed.",
            "state": "completed",
        }

    def _get_recent_stop_result(self, task_id: str) -> dict[str, Any] | None:
        task_state = self.get_recent_task_state(task_id)
        if task_state is None:
            return None

        return self._build_stop_result(task_id, task_state)

    def _get_record_stop_result(
        self,
        task_id: str,
        record: TaskRecord,
    ) -> dict[str, Any] | None:
        if not record.is_terminal:
            return None

        self._remember_terminal_record(record)
        return self._build_stop_result(task_id, record.state)

    async def _handle_backend_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "stop-request":
            await self._handle_remote_stop_request(message)
            return

        if message_type == "stop-event":
            self._handle_remote_stop_event(message)

    async def _handle_remote_stop_request(self, message: dict[str, Any]) -> None:
        task_id = message.get("task_id")
        request_id = message.get("request_id")
        requester_instance_id = message.get("requester_instance_id")

        if not task_id or not request_id or not requester_instance_id:
            log.warning("Ignoring malformed remote stop request: %s", message)
            return

        stop_request = RemoteStopRequest(
            request_id=request_id,
            task_id=task_id,
            requester_instance_id=requester_instance_id,
            requested_at=message.get("requested_at", time.time()),
        )

        handle = self._handles.get(task_id)
        if handle is not None:
            task = handle.task
            # Task already finished before the stop request arrived; clean it up and ack.
            if task is not None and task.done():
                terminal_state = await self.cleanup(task_id, task)
                await self._send_stop_event(
                    requester_instance_id=requester_instance_id,
                    request_id=request_id,
                    task_id=task_id,
                    state=terminal_state,
                )
                return

            # Task is still running: queue the request for ack after cleanup,
            # transition the record to CANCELLING, and signal the asyncio task.
            handle.add_remote_stop_request(stop_request)
            handle.request_stop(stop_request.requested_at)
            await self.backend.save_record(handle.record)
            # Acknowledge immediately with CANCELLING so the requester isn't left waiting.
            await self._send_stop_event(
                requester_instance_id=requester_instance_id,
                request_id=request_id,
                task_id=task_id,
                state=TaskLifecycleState.CANCELLING,
            )

            if task is not None:
                cancel_sent = task.cancel()
                handle.note_cancel_signal_sent(cancel_sent)
            return

        # No live handle; check the local recency cache before hitting the backend.
        recent_record = self._get_recent_terminal_record(task_id)
        if recent_record is not None:
            await self._send_stop_event(
                requester_instance_id=requester_instance_id,
                request_id=request_id,
                task_id=task_id,
                state=recent_record.state,
            )
            return

        # Not in local cache; consult the shared backend record.
        record = await self.backend.load_record(task_id)
        if record is None:
            # Unknown task — cannot ack. The requester will time out and fall back gracefully.
            return

        # Task is already terminal in the shared store; cache it and reply.
        if record.is_terminal:
            self._remember_terminal_record(record)
            await self._send_stop_event(
                requester_instance_id=requester_instance_id,
                request_id=request_id,
                task_id=task_id,
                state=record.state,
            )
            return

        # Shared record says this instance owns the task but _handles has no entry —
        # likely a cleanup race. Attempt a brief reconcile; if resolved, reply.
        # If not, drop silently: the requester will re-check the shared record after timeout.
        if record.owner_instance_id == self.instance_id:
            final_state = await self.reconcile(
                task_id, record=record, timeout_seconds=0.5
            )
            if final_state is not None:
                await self._send_stop_event(
                    requester_instance_id=requester_instance_id,
                    request_id=request_id,
                    task_id=task_id,
                    state=final_state,
                )

    def _handle_remote_stop_event(self, message: dict[str, Any]) -> None:
        request_id = message.get("request_id")
        task_state = message.get("state")

        if request_id is None or task_state is None:
            log.warning("Ignoring malformed remote stop event: %s", message)
            return

        waiter = self._pending_stop_waiters.get(request_id)
        if waiter is None:
            return

        normalized_state = get_task_state(task_state)
        # Track the latest known state for the timeout fallback in _stop_remote_task.
        waiter.last_state = normalized_state

        if is_terminal_state(normalized_state) and not waiter.future.done():
            waiter.future.set_result(normalized_state)

    async def _notify_remote_stop_requests(self, handle: TaskHandle) -> None:
        for stop_request in handle.drain_remote_stop_requests():
            await self._send_stop_event(
                requester_instance_id=stop_request.requester_instance_id,
                request_id=stop_request.request_id,
                task_id=stop_request.task_id,
                state=handle.record.state,
            )

    async def _send_stop_event(
        self,
        *,
        requester_instance_id: str,
        request_id: str,
        task_id: str,
        state: TaskLifecycleState | str,
    ) -> None:
        normalized_state = get_task_state(state)
        await self.backend.send_message(
            requester_instance_id,
            {
                "type": "stop-event",
                "request_id": request_id,
                "task_id": task_id,
                "owner_instance_id": self.instance_id,
                "state": normalized_state.value,
                "updated_at": time.time(),
            },
        )
