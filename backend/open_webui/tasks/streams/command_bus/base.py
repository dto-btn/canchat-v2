import asyncio
from typing import Protocol, runtime_checkable

from open_webui.tasks.streams.models import StopStreamCommand


@runtime_checkable
class CommandBus(Protocol):
    async def subscribe(self) -> asyncio.Queue:
        """Return a queue receiving decoded command objects."""

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Detach a queue from the bus."""

    async def publish(self, command: StopStreamCommand) -> None:
        """Publish a command to all subscribers."""
