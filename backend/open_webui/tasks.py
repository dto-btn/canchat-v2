"""Registry of in-process background tasks with lifecycle metrics (OBS-30).

Tasks live only in this process; this is not a durable queue. Every task is
finalized exactly once: when it ends, when ``stop_task`` stops it, or by
``shutdown_tasks`` at application shutdown, which also finalizes tasks that
do not stop in time. Finalizing removes the task from the registry, records
``canchat.tasks.finished`` and calls the task's ``on_finish`` hook, so the
operation a task delivers (e.g. a chat operation) is never left open.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import uuid4

from open_webui.metrics.service import MetricsService, get_metrics_service
from open_webui.observability.chat import (
    OUTCOME_CANCELLED,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
)

TASKS_ACTIVE = "canchat.tasks.active"
TASKS_FINISHED = "canchat.tasks.finished"

# Wait for cancelled tasks to run their cleanup before finalizing them.
SHUTDOWN_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0

TaskFinalizer = Callable[[str, BaseException | None], None]

# Active tasks by ID.
tasks: dict[str, asyncio.Task] = {}
_finalizers: dict[str, TaskFinalizer] = {}

log = logging.getLogger(__name__)


def register_task_metrics(metrics_service: MetricsService | None) -> None:
    """Report the number of active tasks through ``canchat.tasks.active``."""
    if metrics_service is not None and metrics_service.has_instrument(TASKS_ACTIVE):
        metrics_service.register_callback(TASKS_ACTIVE, lambda: [(len(tasks), None)])


def _task_outcome(task: asyncio.Task) -> tuple[str, BaseException | None]:
    if task.cancelled():
        return OUTCOME_CANCELLED, None
    error = task.exception()
    if error is not None:
        return OUTCOME_ERROR, error
    # A coroutine may handle CancelledError and return normally; the pending
    # cancellation request still marks the task as cancelled.
    if task.cancelling():
        return OUTCOME_CANCELLED, None
    return OUTCOME_SUCCESS, None


def _record_outcome(outcome: str, error: BaseException | None) -> None:
    metrics = get_metrics_service()
    if metrics is None or not metrics.has_instrument(TASKS_FINISHED):
        return
    attrs: dict[str, Any] = {"outcome": outcome}
    if error is not None:
        attrs["error.type"] = type(error).__qualname__
    try:
        metrics.record(TASKS_FINISHED, 1, attrs)
    except Exception as e:
        # MetricsService raises only in strict (test) mode.
        if metrics.strict:
            raise
        log.warning("Failed to record task outcome: %s", type(e).__name__)


def _finalize(task_id: str, outcome: str, error: BaseException | None) -> bool:
    """Finalize ``task_id`` once; later calls return ``False``."""
    if tasks.pop(task_id, None) is None:
        return False
    finalizer = _finalizers.pop(task_id, None)
    if outcome == OUTCOME_ERROR and error is not None:
        # The logging pipeline keeps frames and exception types only.
        log.error("Background task %s failed", task_id, exc_info=error)
    try:
        _record_outcome(outcome, error)
    finally:
        # Runs even when strict mode re-raises, so the operation always ends.
        if finalizer is not None:
            try:
                finalizer(outcome, error)
            except Exception as e:
                log.warning("Task finalizer failed: %s", type(e).__name__)
    return True


def _finalize_done(task_id: str, task: asyncio.Task) -> bool:
    # Retrieving the exception also avoids "exception was never retrieved".
    outcome, error = _task_outcome(task)
    return _finalize(task_id, outcome, error)


def create_task(
    coroutine: Coroutine[Any, Any, Any], *, on_finish: TaskFinalizer | None = None
) -> tuple[str, asyncio.Task]:
    """
    Create a new asyncio task and add it to the registry.

    ``on_finish(outcome, error)`` is called exactly once when the task is
    finalized, with ``outcome`` one of ``success``, ``error`` or ``cancelled``.
    """
    task_id = str(uuid4())
    task = asyncio.create_task(coroutine)
    tasks[task_id] = task
    if on_finish is not None:
        _finalizers[task_id] = on_finish
    task.add_done_callback(lambda t: _finalize_done(task_id, t))
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


async def stop_task(
    task_id: str, timeout: float = STOP_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """
    Cancel a running task, wait for it to end and finalize it.

    The task gets ``timeout`` seconds to run its cancellation handling; if it
    is still running after that, it is finalized as cancelled.
    """
    task = tasks.get(task_id)
    if not task:
        raise ValueError(f"Task with ID {task_id} not found.")

    task.cancel()
    # Waits without re-raising the task's result, so a cancellation of this
    # caller is not mistaken for the task's own.
    _, pending = await asyncio.wait({task}, timeout=timeout)
    if pending:
        log.warning(
            "Background task %s did not stop within %ss; finalized as cancelled",
            task_id,
            timeout,
        )
        _finalize(task_id, OUTCOME_CANCELLED, None)
    else:
        _finalize_done(task_id, task)
    return {"status": True, "message": f"Task {task_id} successfully stopped."}


async def shutdown_tasks(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> int:
    """
    Cancel every active task at process shutdown and finalize each one once.

    Tasks get ``timeout`` seconds to run their cancellation handling; any
    still running are finalized as cancelled so their operations end before
    telemetry is flushed. Returns the number of tasks that were active.
    """
    active = dict(tasks)
    if not active:
        return 0

    running = [task for task in active.values() if not task.done()]
    for task in running:
        task.cancel()
    log.info("Cancelling %d background task(s) for shutdown", len(running))

    pending: set[asyncio.Task] = set()
    if running:
        _, pending = await asyncio.wait(running, timeout=timeout)

    # Tasks that ended are usually finalized by their done callback already;
    # these calls are then no-ops.
    for task_id, task in active.items():
        if task.done():
            _finalize_done(task_id, task)
        else:
            _finalize(task_id, OUTCOME_CANCELLED, None)
    if pending:
        log.warning(
            "%d background task(s) did not stop within %ss; finalized as cancelled",
            len(pending),
            timeout,
        )
    return len(active)
