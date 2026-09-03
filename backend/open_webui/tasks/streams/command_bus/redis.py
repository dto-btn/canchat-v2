import asyncio
import json
import logging
from urllib.parse import urlparse

from pydantic import ValidationError

from open_webui.tasks.streams.command_bus.base import StreamBusMessage
from open_webui.tasks.streams.models import StopCompletedEvent, StopStreamCommand

log = logging.getLogger(__name__)


class RedisCommandBus:
    """Redis pub/sub adapter for stream coordination commands."""

    def __init__(
        self,
        redis_url: str,
        channel: str = "open-webui:stream-commands",
        socket_connect_timeout: float = 2.0,
        # socket_timeout=None: no read deadline on the subscriber; listen() is a
        # blocking long-poll and would raise TimeoutError on every idle interval.
        socket_timeout: float | None = None,
        socket_keepalive: bool = True,
        retry_on_timeout: bool = True,
        health_check_interval: int = 30,
    ) -> None:
        self._validate_redis_url(redis_url)
        self.redis_url = redis_url
        self.channel = channel
        self._socket_connect_timeout = socket_connect_timeout
        self._socket_timeout = socket_timeout
        self._socket_keepalive = socket_keepalive
        self._retry_on_timeout = retry_on_timeout
        self._health_check_interval = health_check_interval
        self._redis = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None

    @staticmethod
    def _validate_redis_url(redis_url: str) -> None:
        try:
            parsed = urlparse(redis_url)
            hostname = parsed.hostname
            parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "TASK_COORDINATION_URL must be a valid Redis URL."
            ) from exc

        if parsed.scheme not in {"redis", "rediss", "unix"}:
            raise ValueError(
                "TASK_COORDINATION_URL must use a redis://, rediss://, or unix:// URL."
            )

        if parsed.scheme == "unix":
            if parsed.netloc or not parsed.path:
                raise ValueError(
                    "TASK_COORDINATION_URL must include a socket path for unix:// URLs."
                )
            return

        if not hostname:
            raise ValueError(
                "TASK_COORDINATION_URL must include a Redis host when using redis:// or rediss://."
            )

    async def _ensure_client(self) -> None:
        if self._redis is not None:
            return
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis command bus requires the 'redis' package with asyncio support"
            ) from exc
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=self._socket_connect_timeout,
            socket_timeout=self._socket_timeout,
            socket_keepalive=self._socket_keepalive,
            retry_on_timeout=self._retry_on_timeout,
            health_check_interval=self._health_check_interval,
        )

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

    async def publish(self, message: StreamBusMessage) -> None:
        await self._ensure_client()
        await self._redis.publish(self.channel, message.model_dump_json())

    async def _reader_loop(self) -> None:
        if self._pubsub is None or self._queue is None:
            log.warning("Redis command bus reader started without pubsub or queue")
            return

        while True:
            try:
                async for raw in self._pubsub.listen():
                    if not isinstance(raw, dict) or raw.get("type") != "message":
                        continue
                    data = raw.get("data")
                    if not data:
                        continue
                    try:
                        payload = json.loads(data)
                        if not isinstance(payload, dict):
                            continue
                        msg_type = payload.get("type")
                        if msg_type == "stop_stream":
                            msg = StopStreamCommand.model_validate(payload)
                        elif msg_type == "stop_completed":
                            msg = StopCompletedEvent.model_validate(payload)
                        else:
                            continue
                        await self._queue.put(msg)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        log.debug("Ignoring invalid stream bus payload: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Redis command bus reader error: %s", exc)
                await asyncio.sleep(0.5)
