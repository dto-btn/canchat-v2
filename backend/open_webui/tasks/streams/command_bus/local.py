import asyncio

from open_webui.tasks.streams.command_bus.base import StreamBusMessage


class LocalCommandBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subs.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(queue)

    async def publish(self, message: StreamBusMessage) -> None:
        async with self._lock:
            subscribers = list(self._subs)
        for queue in subscribers:
            await queue.put(message)
