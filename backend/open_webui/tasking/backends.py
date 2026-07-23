from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis

from .models import TaskLifecycleState, TaskRecord

TaskMessageHandler = Callable[[dict[str, Any]], Awaitable[None]]

log = logging.getLogger(__name__)


class TaskCoordinationBackend(ABC):
    @abstractmethod
    async def start(
        self,
        instance_id: str,
        message_handler: TaskMessageHandler,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def load_record(self, task_id: str) -> TaskRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def save_record(self, record: TaskRecord) -> TaskRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def send_message(
        self, target_instance_id: str, message: dict[str, Any]
    ) -> bool:
        raise NotImplementedError


class InMemoryTaskHub:
    def __init__(
        self,
        *,
        active_task_ttl_seconds: int,
        recent_task_ttl_seconds: int,
    ):
        self.active_task_ttl_seconds = active_task_ttl_seconds
        self.recent_task_ttl_seconds = recent_task_ttl_seconds
        self._records: dict[str, tuple[TaskRecord, float]] = {}
        self._handlers: dict[str, TaskMessageHandler] = {}

    def _ttl_for_record(self, record: TaskRecord) -> int:
        # Active tasks need a long TTL to survive instance restarts or slow cleanups.
        # Terminal records use the short recency TTL — they're only kept for late lookups.
        if record.state in (
            TaskLifecycleState.RUNNING,
            TaskLifecycleState.CANCELLING,
        ):
            return self.active_task_ttl_seconds

        return self.recent_task_ttl_seconds

    def _prune_records(self) -> None:
        now = time.monotonic()
        expired_task_ids = [
            task_id
            for task_id, (_, expires_at) in self._records.items()
            if expires_at <= now
        ]

        for task_id in expired_task_ids:
            self._records.pop(task_id, None)

    async def register_handler(
        self,
        instance_id: str,
        handler: TaskMessageHandler,
    ) -> None:
        self._handlers[instance_id] = handler

    async def unregister_handler(self, instance_id: str) -> None:
        self._handlers.pop(instance_id, None)

    async def load_record(self, task_id: str) -> TaskRecord | None:
        self._prune_records()
        record_entry = self._records.get(task_id)
        if record_entry is None:
            return None

        record, _ = record_entry
        return record.clone()

    async def save_record(self, record: TaskRecord) -> TaskRecord:
        self._prune_records()
        expires_at = time.monotonic() + self._ttl_for_record(record)
        # Clone when storing so the hub owns its copy; the caller retains the original.
        self._records[record.task_id] = (record.clone(), expires_at)
        return record

    async def send_message(
        self, target_instance_id: str, message: dict[str, Any]
    ) -> bool:
        handler = self._handlers.get(target_instance_id)
        if handler is None:
            return False

        asyncio.create_task(handler(dict(message)))
        return True

    def create_backend(self) -> TaskCoordinationBackend:
        # The hub owns the shared in-memory coordination plane, but each TaskManager
        # still needs its own start/close lifecycle bound to one instance_id.
        return _BoundInMemoryTaskHubBackend(self)


class _BoundInMemoryTaskHubBackend(TaskCoordinationBackend):
    """Per-manager binding over the shared in-memory coordination hub."""

    def __init__(self, hub: InMemoryTaskHub):
        self._hub = hub
        self._instance_id: str | None = None

    async def start(
        self,
        instance_id: str,
        message_handler: TaskMessageHandler,
    ) -> None:
        self._instance_id = instance_id
        await self._hub.register_handler(instance_id, message_handler)

    async def close(self) -> None:
        if self._instance_id is None:
            return

        await self._hub.unregister_handler(self._instance_id)
        self._instance_id = None

    async def load_record(self, task_id: str) -> TaskRecord | None:
        return await self._hub.load_record(task_id)

    async def save_record(self, record: TaskRecord) -> TaskRecord | None:
        return await self._hub.save_record(record)

    async def send_message(
        self, target_instance_id: str, message: dict[str, Any]
    ) -> bool:
        return await self._hub.send_message(target_instance_id, message)


class RedisTaskCoordinationBackend(TaskCoordinationBackend):
    def __init__(
        self,
        *,
        redis_url: str,
        record_key_prefix: str,
        instance_channel_prefix: str,
        active_task_ttl_seconds: int,
        recent_task_ttl_seconds: int,
        listener_poll_interval_seconds: float,
    ):
        self.redis_url = redis_url
        self.record_key_prefix = record_key_prefix
        self.instance_channel_prefix = instance_channel_prefix
        self.active_task_ttl_seconds = active_task_ttl_seconds
        self.recent_task_ttl_seconds = recent_task_ttl_seconds
        self.listener_poll_interval_seconds = listener_poll_interval_seconds

        self._instance_id: str | None = None
        self._message_handler: TaskMessageHandler | None = None
        self._client: redis.Redis | None = None
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None

    def _record_key(self, task_id: str) -> str:
        return f"{self.record_key_prefix}:{task_id}"

    def _instance_channel(self, instance_id: str) -> str:
        return f"{self.instance_channel_prefix}:{instance_id}"

    def _ttl_for_record(self, record: TaskRecord) -> int:
        if record.state in (
            TaskLifecycleState.RUNNING,
            TaskLifecycleState.CANCELLING,
        ):
            return self.active_task_ttl_seconds

        return self.recent_task_ttl_seconds

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)

        return self._client

    async def start(
        self,
        instance_id: str,
        message_handler: TaskMessageHandler,
    ) -> None:
        self._instance_id = instance_id
        self._message_handler = message_handler

        if self._listener_task is not None:
            if not self._listener_task.done():
                return
            # Listener died without being explicitly cancelled — unexpected; restart it.
            log.warning(
                "Task coordination listener exited unexpectedly (state=%s). Restarting.",
                self._listener_task._state,
            )

        self._listener_task = asyncio.create_task(self._listen_for_instance_messages())

    async def close(self) -> None:
        listener_task = self._listener_task
        self._listener_task = None

        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        if self._pubsub is not None:
            try:
                channel_name = self._instance_channel(self._instance_id or "")
                await self._pubsub.unsubscribe(channel_name)
            except Exception:
                pass

            # redis-py >= 4.2 exposes aclose(); fall back to close() for older versions.
            close = getattr(self._pubsub, "aclose", None)
            if close is None:
                close = self._pubsub.close

            try:
                await close()
            except Exception:
                pass

            self._pubsub = None

        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

        self._instance_id = None
        self._message_handler = None

    async def load_record(self, task_id: str) -> TaskRecord | None:
        try:
            client = await self._get_client()
            raw_value = await client.get(self._record_key(task_id))
        except Exception as exc:
            log.warning("Failed to read shared task record for %s: %s", task_id, exc)
            return None

        if raw_value is None:
            return None

        try:
            return TaskRecord.from_payload(json.loads(raw_value))
        except Exception:
            log.warning("Ignoring invalid shared task record for %s", task_id)
            return None

    async def save_record(self, record: TaskRecord) -> TaskRecord | None:
        try:
            client = await self._get_client()
            payload = record.to_payload()
            await client.set(
                self._record_key(record.task_id),
                json.dumps(payload),
                ex=self._ttl_for_record(record),
            )
            return record
        except Exception as exc:
            log.warning(
                "Failed to update shared task record for %s: %s",
                record.task_id,
                exc,
            )
            return None

    async def send_message(
        self, target_instance_id: str, message: dict[str, Any]
    ) -> bool:
        try:
            client = await self._get_client()
            await client.publish(
                self._instance_channel(target_instance_id),
                json.dumps(message),
            )
            return True
        except Exception as exc:
            log.warning(
                "Failed to publish task coordination message to %s: %s",
                target_instance_id,
                exc,
            )
            return False

    async def _listen_for_instance_messages(self) -> None:
        if self._instance_id is None:
            return

        channel_name = self._instance_channel(self._instance_id)
        try:
            client = await self._get_client()
            self._pubsub = client.pubsub()
            await self._pubsub.subscribe(channel_name)

            # Poll-based loop: get_message returns None when the queue is empty;
            # `timeout` acts as a non-blocking sleep between checks.
            while True:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=self.listener_poll_interval_seconds,
                )

                if not message:
                    continue

                payload = message.get("data")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")

                try:
                    parsed_message = json.loads(payload)
                except Exception:
                    log.warning(
                        "Ignoring malformed task coordination payload: %s", payload
                    )
                    continue

                if self._message_handler is None:
                    continue

                try:
                    await self._message_handler(parsed_message)
                except Exception as exc:
                    log.error("Task coordination message handler failed: %s", exc)
        except asyncio.CancelledError:
            raise  # Propagate normal shutdown; do not treat as an error.
        except Exception as exc:
            log.error("Shared task coordination listener failed: %s", exc)
