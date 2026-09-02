import asyncio
import time
from enum import Enum
from typing import Any, Coroutine, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StreamStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"


class StopStreamCommand(BaseModel):
    type: Literal["stop_stream"] = "stop_stream"
    stream_id: str
    source_instance_id: str


class StopCompletedEvent(BaseModel):
    type: Literal["stop_completed"] = "stop_completed"
    stream_id: str
    terminal_state: str  # "cancelled" | "completed" | "failed"


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
        return self.model_dump(exclude={"task", "coroutine"})
