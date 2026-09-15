import asyncio
from typing import Protocol, Union, runtime_checkable

from open_webui.tasks.streams.models import StopCompletedEvent, StopStreamCommand

StreamBusMessage = Union[StopStreamCommand, StopCompletedEvent]


@runtime_checkable
class CommandBus(Protocol):
    async def subscribe(self) -> asyncio.Queue:
        """Return a queue receiving decoded message objects."""

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Detach a queue from the bus."""

    async def publish(self, message: StreamBusMessage) -> None:
        """Publish a message to all subscribers."""
