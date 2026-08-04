import asyncio
import time
from enum import Enum
from typing import Any, Coroutine, Optional

from pydantic import BaseModel, ConfigDict, Field


class StreamStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"


class StopStreamCommand(BaseModel):
    stream_id: str
    target_instance_id: Optional[str] = None


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
