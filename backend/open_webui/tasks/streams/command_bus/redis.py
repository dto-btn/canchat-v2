import asyncio
import json
import logging

from open_webui.tasks.streams.models import StopStreamCommand

log = logging.getLogger(__name__)


class RedisCommandBus:
    """Redis pub/sub adapter for stop-stream commands."""

    def __init__(
        self,
        redis_url: str,
        channel: str = "open-webui:stream-commands",
    ) -> None:
        self.redis_url = redis_url
        self.channel = channel
        self._redis = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None

    async def _ensure_client(self):
        if self._redis is not None:
            return
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis command bus requires the 'redis' package with asyncio support"
            ) from exc

        self._redis = redis.from_url(self.redis_url, decode_responses=True)

    async def subscribe(self) -> asyncio.Queue:
        await self._ensure_client()
        if (
            self._queue is not None
            and self._reader_task
            and not self._reader_task.done()
        ):
            return self._queue

        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self.channel)

        self._queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="redis-command-bus-reader"
        )
        return self._queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._pubsub is not None:
            await self._pubsub.unsubscribe(self.channel)
            await self._pubsub.close()
            self._pubsub = None

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

        self._queue = None

    async def publish(self, command: StopStreamCommand) -> None:
        await self._ensure_client()
        payload = json.dumps(command.model_dump())
        await self._redis.publish(self.channel, payload)

    async def _reader_loop(self) -> None:
        while True:
            if self._pubsub is None or self._queue is None:
                return
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    await asyncio.sleep(0)
                    continue

                data = message.get("data")
                if not data:
                    continue

                try:
                    cmd = StopStreamCommand.model_validate_json(data)
                except Exception:
                    cmd = StopStreamCommand.model_validate(json.loads(data))
                await self._queue.put(cmd)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Redis command bus reader error: %s", exc)
                await asyncio.sleep(0.5)
