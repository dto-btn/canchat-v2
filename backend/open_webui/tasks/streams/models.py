import asyncio
import time
from enum import Enum
from typing import Any, Coroutine, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StreamStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"


TerminalState = Literal["cancelled", "completed", "failed"]
StopErrorCode = Literal["forbidden", "not_found"]


class StopStreamCommand(BaseModel):
    type: Literal["stop_stream"] = "stop_stream"
    stream_id: str
    request_id: str
    source_instance_id: str
    requester_user_id: str
    requester_is_admin: bool = False


class StopCompletedEvent(BaseModel):
    type: Literal["stop_completed"] = "stop_completed"
    stream_id: str
    request_id: str
    terminal_state: Optional[TerminalState] = None
    error_code: Optional[StopErrorCode] = None


class StreamRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    task: Optional[asyncio.Task] = None
    coroutine: Coroutine
    status: StreamStatus = StreamStatus.PENDING
    metadata: dict[str, Any]
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))

    def public(self) -> dict[str, Any]:
        return self.model_dump(exclude={"task"})

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
