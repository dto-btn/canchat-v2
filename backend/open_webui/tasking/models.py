from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskLifecycleState(str, Enum):
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_TASK_STATES = frozenset(
    {
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.COMPLETED,
        TaskLifecycleState.FAILED,
    }
)

TASK_RECORD_RESERVED_KEYS = frozenset(
    {
        "task_id",
        "owner_instance_id",
        "state",
        "created_at",
        "updated_at",
        "stop_requested",
        "stop_requested_at",
        "terminal_reason",
    }
)


def get_task_state(value: TaskLifecycleState | str) -> TaskLifecycleState:
    if isinstance(value, TaskLifecycleState):
        return value

    return TaskLifecycleState(value)


def is_terminal_state(value: TaskLifecycleState | str) -> bool:
    return get_task_state(value) in TERMINAL_TASK_STATES


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    owner_instance_id: str
    state: TaskLifecycleState
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    stop_requested_at: float | None = None
    terminal_reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        return is_terminal_state(self.state)

    def touch(self) -> None:
        self.updated_at = time.time()

    def request_stop(self, requested_at: float | None = None) -> bool:
        self.stop_requested = True
        if self.stop_requested_at is None:
            self.stop_requested_at = requested_at or time.time()

        if self.state == TaskLifecycleState.RUNNING:
            self.state = TaskLifecycleState.CANCELLING
            self.touch()
            return True

        self.touch()
        return False

    def mark_terminal(
        self,
        state: TaskLifecycleState | str,
        reason: str | None = None,
    ) -> None:
        self.state = get_task_state(state)
        self.terminal_reason = reason
        self.touch()

    def clone(self) -> "TaskRecord":
        return TaskRecord(
            task_id=self.task_id,
            owner_instance_id=self.owner_instance_id,
            state=self.state,
            metadata=dict(self.metadata),
            created_at=self.created_at,
            updated_at=self.updated_at,
            stop_requested=self.stop_requested,
            stop_requested_at=self.stop_requested_at,
            terminal_reason=self.terminal_reason,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            **self.metadata,
            "task_id": self.task_id,
            "owner_instance_id": self.owner_instance_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stop_requested": self.stop_requested,
        }

        if self.stop_requested_at is not None:
            payload["stop_requested_at"] = self.stop_requested_at

        if self.terminal_reason is not None:
            payload["terminal_reason"] = self.terminal_reason

        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskRecord":
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in TASK_RECORD_RESERVED_KEYS
        }

        return cls(
            task_id=payload["task_id"],
            owner_instance_id=payload["owner_instance_id"],
            state=get_task_state(payload["state"]),
            metadata=metadata,
            created_at=payload.get(
                "created_at", payload.get("updated_at", time.time())
            ),
            updated_at=payload.get("updated_at", time.time()),
            stop_requested=bool(payload.get("stop_requested", False)),
            stop_requested_at=payload.get("stop_requested_at"),
            terminal_reason=payload.get("terminal_reason"),
        )


@dataclass(slots=True)
class RemoteStopRequest:
    request_id: str
    task_id: str
    requester_instance_id: str
    requested_at: float


@dataclass(slots=True)
class TaskHandle:
    record: TaskRecord
    terminal_future: asyncio.Future[TaskLifecycleState]
    task: asyncio.Task | None = None
    cancel_signal_sent: bool = False
    remote_stop_requests: dict[str, RemoteStopRequest] = field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return self.record.task_id

    @property
    def state(self) -> TaskLifecycleState:
        return self.record.state

    def bind_task(self, task: asyncio.Task) -> None:
        self.task = task

    def request_stop(self, requested_at: float | None = None) -> bool:
        return self.record.request_stop(requested_at)

    def note_cancel_signal_sent(self, cancel_sent: bool) -> None:
        if cancel_sent:
            self.cancel_signal_sent = True

    def add_remote_stop_request(self, request: RemoteStopRequest) -> None:
        self.remote_stop_requests[request.request_id] = request

    def drain_remote_stop_requests(self) -> list[RemoteStopRequest]:
        requests = list(self.remote_stop_requests.values())
        self.remote_stop_requests.clear()
        return requests

    def mark_terminal(
        self,
        state: TaskLifecycleState | str,
        reason: str | None = None,
    ) -> TaskLifecycleState:
        terminal_state = get_task_state(state)
        self.record.mark_terminal(terminal_state, reason=reason)
        if not self.terminal_future.done():
            self.terminal_future.set_result(terminal_state)
        return terminal_state
