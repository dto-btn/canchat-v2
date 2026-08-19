import asyncio

from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.manager import StreamManager


class RecordingLocalCommandBus(LocalCommandBus):
    def __init__(self) -> None:
        super().__init__()
        self.published_messages: list[object] = []

    async def publish(self, message) -> None:
        self.published_messages.append(message)
        await super().publish(message)


def make_test_stream_manager(
    instance_id: str,
    *,
    bus: LocalCommandBus | None = None,
    remote_stop_timeout: float = 0.5,
) -> StreamManager:
    shared_bus = bus or LocalCommandBus()
    return StreamManager(
        bus=shared_bus,
        instance_id=instance_id,
        remote_stop_timeout=remote_stop_timeout,
    )


def test_create_stream_starts_running():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def wait_for_signal():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, _stream_task = await manager.create(
            wait_for_signal(),
            metadata={"chat_id": "chat-id"},
        )
        await started.wait()

        record = await manager.get(stream_id)
        await manager.close()

        assert record is not None
        assert record["id"] == stream_id
        assert record["metadata"] == {"chat_id": "chat-id"}

    asyncio.run(scenario())


def test_local_stop_cancels_stream_without_publishing_remote_command():
    async def scenario():
        bus = RecordingLocalCommandBus()
        manager = make_test_stream_manager(instance_id="owner", bus=bus)
        await manager.start()
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, stream_task = await manager.create(wait_forever())
        await started.wait()

        result = await manager.stop(stream_id)
        record = await manager.get(stream_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} successfully stopped.",
            "state": "cancelled",
        }
        assert stream_task.cancelled()
        assert record is None
        assert bus.published_messages == []

    asyncio.run(scenario())


def test_stop_returns_completed_when_stream_finishes_before_cleanup():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()

        async def complete_immediately():
            return "done"

        stream_id, stream_task = await manager.create(complete_immediately())
        await stream_task
        # _finalize is scheduled but hasn't run yet — the done task can still be stopped.

        result = await manager.stop(stream_id)
        record = await manager.get(stream_id)
        await asyncio.sleep(0)  # let _finalize finish if it was already queued
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} already completed.",
            "state": "completed",
        }
        assert record is None

    asyncio.run(scenario())


def test_stop_raises_for_unknown_stream_id():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()

        try:
            await manager.stop("missing-stream")
        except ValueError as exc:
            assert str(exc) == "Task with ID missing-stream not found."
        else:
            raise AssertionError("Expected ValueError for missing stream")
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_stop_marks_swallowed_cancellation_as_cancelled_and_removes_stream():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def swallow_cancelled_error():
            started.set()
            try:
                while True:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                return "partial"

        stream_id, _stream_task = await manager.create(swallow_cancelled_error())
        await started.wait()

        result = await manager.stop(stream_id)
        record = await manager.get(stream_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} successfully stopped.",
            "state": "cancelled",
        }
        assert record is None

    asyncio.run(scenario())


def test_remote_stop_returns_cancelled_state_for_swallowed_cancellation():
    async def scenario():
        bus = LocalCommandBus()
        owner = make_test_stream_manager(instance_id="owner", bus=bus)
        requester = make_test_stream_manager(
            instance_id="requester",
            bus=bus,
            remote_stop_timeout=2.0,
        )
        await owner.start()
        await requester.start()
        started = asyncio.Event()

        async def swallow_cancelled_error():
            started.set()
            try:
                while True:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                return "partial"

        stream_id, _stream_task = await owner.create(
            swallow_cancelled_error(),
            metadata={"chat_id": "chat-1"},
        )
        await started.wait()

        result = await requester.stop(stream_id)
        await owner.close()
        await requester.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} successfully stopped.",
            "state": "cancelled",
        }

    asyncio.run(scenario())


def test_self_echo_does_not_resolve_pending_stop():
    """A pod should not process stop commands it published itself."""

    async def scenario():
        bus = LocalCommandBus()
        manager = make_test_stream_manager(
            instance_id="sole-pod",
            bus=bus,
            remote_stop_timeout=0.1,
        )
        await manager.start()

        try:
            await manager.stop("nonexistent-stream")
        except ValueError as exc:
            assert str(exc) == "Task with ID nonexistent-stream not found."
        else:
            raise AssertionError(
                "Expected ValueError — self-echo must not resolve the stop"
            )
        finally:
            await manager.close()

    asyncio.run(scenario())
