# registry.py
import asyncio
import time
from typing import Coroutine, Optional, Any

from open_webui.tasks.streams.models import StreamRecord, StreamStatus


class StreamRegistry:
    def __init__(self) -> None:
        self._items: dict[str, StreamRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        stream_id: str,
        coroutine: Coroutine,
        metadata: Optional[dict[str, Any]] = None,
    ) -> StreamRecord:
        async with self._lock:
            if stream_id in self._items:
                raise ValueError(f"Task with ID {stream_id} already exists.")
            try:
                rec = StreamRecord(
                    id=stream_id, coroutine=coroutine, metadata=metadata or {}
                )
                self._items[stream_id] = rec
                return rec
            except Exception as e:
                raise ValueError(f"Failed to create stream record: {e}")

    async def get(self, stream_id: str) -> Optional[StreamRecord]:
        async with self._lock:
            rec = self._items.get(stream_id)
            return None if rec is None else rec.model_copy(deep=False)

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [rec.public() for rec in self._items.values()]

    async def update(
        self,
        stream_id: str,
        task: Optional[asyncio.Task] = None,
        status: Optional[StreamStatus] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            rec = self._items.get(stream_id)
            if rec is None:
                raise KeyError(f"Task with ID {stream_id} not found.")

            if task is not None:
                rec.task = task
            if status is not None:
                rec.status = status
            if metadata is not None:
                rec.metadata = metadata

            rec.updated_at = int(time.time())

    async def mark_cancelling(self, stream_id: str) -> None:
        async with self._lock:
            rec = self._items.get(stream_id)
            if rec is None:
                raise KeyError(f"Task with ID {stream_id} not found.")
            rec.status = StreamStatus.CANCELLING
            rec.updated_at = int(time.time())

    async def remove(self, stream_id: str) -> None:
        async with self._lock:
            if stream_id in self._items:
                del self._items[stream_id]
