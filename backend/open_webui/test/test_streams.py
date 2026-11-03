import asyncio

from open_webui.tasks.streams.command_bus.local import LocalCommandBus
from open_webui.tasks.streams.command_bus.redis import RedisCommandBus
from open_webui.tasks.streams.manager import StreamManager


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
        assert "task" not in record
        assert "coroutine" not in record

    asyncio.run(scenario())


def test_local_stop_cancels_stream_and_removes_record():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, stream_task = await manager.create(
            wait_forever(),
            metadata={"user_id": "user-1"},
        )
        await started.wait()

        result = await manager.stop(stream_id, requester_user_id="user-1")
        record = await manager.get(stream_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} successfully stopped.",
            "state": "cancelled",
        }
        assert stream_task.cancelled()
        assert record is None

    asyncio.run(scenario())


def test_stop_returns_completed_when_stream_finishes_before_cleanup():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()

        async def complete_immediately():
            return "done"

        stream_id, stream_task = await manager.create(
            complete_immediately(),
            metadata={"user_id": "user-1"},
        )
        await stream_task
        # _finalize is scheduled but hasn't run yet — the done task can still be stopped.

        result = await manager.stop(stream_id, requester_user_id="user-1")
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
            await manager.stop("missing-stream", requester_user_id="user-1")
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

        stream_id, _stream_task = await manager.create(
            swallow_cancelled_error(),
            metadata={"user_id": "user-1"},
        )
        await started.wait()

        result = await manager.stop(stream_id, requester_user_id="user-1")
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
            metadata={"chat_id": "chat-1", "user_id": "user-1"},
        )
        await started.wait()

        result = await requester.stop(stream_id, requester_user_id="user-1")
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
            await manager.stop("nonexistent-stream", requester_user_id="user-1")
        except ValueError as exc:
            assert str(exc) == "Task with ID nonexistent-stream not found."
        else:
            raise AssertionError(
                "Expected ValueError — self-echo must not resolve the stop"
            )
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_redis_command_bus_validates_url_format():
    for url in (
        "redis://localhost:6379/0",
        "rediss://localhost",
        "unix:///tmp/redis.sock",
    ):
        RedisCommandBus(url)

    for url in (
        "",
        "http://localhost",
        "redis://",
        "redis://localhost:not-a-port",
        "unix://",
    ):
        try:
            RedisCommandBus(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid Redis URL to be rejected: {url}")


def test_list_filters_tasks_to_requesting_user_and_summarizes_records():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        first_stream_id, _ = await manager.create(
            wait_forever(),
            metadata={"user_id": "user-1", "chat_id": "chat-1"},
        )
        await started.wait()
        started.clear()

        second_stream_id, _ = await manager.create(
            wait_forever(),
            metadata={"user_id": "user-2", "chat_id": "chat-2"},
        )
        await started.wait()

        user_tasks = await manager.list(requester_user_id="user-1")
        admin_tasks = await manager.list(
            requester_user_id="admin-user",
            requester_is_admin=True,
        )

        await manager.stop(first_stream_id, requester_user_id="user-1")
        await manager.stop(second_stream_id, requester_user_id="user-2")
        await manager.close()

        assert len(user_tasks) == 1
        assert user_tasks[0]["id"] == first_stream_id
        assert set(user_tasks[0]) == {"id", "status", "created_at", "updated_at"}
        assert {task["id"] for task in admin_tasks} == {
            first_stream_id,
            second_stream_id,
        }

    asyncio.run(scenario())


def test_local_stop_rejects_non_owner():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, _stream_task = await manager.create(
            wait_forever(),
            metadata={"user_id": "owner-user"},
        )
        await started.wait()

        try:
            await manager.stop(stream_id, requester_user_id="other-user")
        except PermissionError as exc:
            assert (
                str(exc)
                == f"Task with ID {stream_id} does not belong to the authenticated user."
            )
        else:
            raise AssertionError("Expected PermissionError for non-owner stop")

        await manager.stop(stream_id, requester_user_id="owner-user")
        await manager.close()

    asyncio.run(scenario())


def test_remote_stop_rejects_non_owner():
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

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, _stream_task = await owner.create(
            wait_forever(),
            metadata={"user_id": "owner-user"},
        )
        await started.wait()

        try:
            await requester.stop(stream_id, requester_user_id="other-user")
        except PermissionError as exc:
            assert (
                str(exc)
                == f"Task with ID {stream_id} does not belong to the authenticated user."
            )
        else:
            raise AssertionError("Expected PermissionError for remote non-owner stop")

        await owner.stop(stream_id, requester_user_id="owner-user")
        await owner.close()
        await requester.close()

    asyncio.run(scenario())


def test_remote_stop_ignores_unrelated_pods_until_owner_replies():
    async def scenario():
        bus = LocalCommandBus()
        owner = make_test_stream_manager(instance_id="owner", bus=bus)
        bystander = make_test_stream_manager(instance_id="bystander", bus=bus)
        requester = make_test_stream_manager(
            instance_id="requester",
            bus=bus,
            remote_stop_timeout=2.0,
        )
        await owner.start()
        await bystander.start()
        await requester.start()
        started = asyncio.Event()

        async def wait_forever_with_slow_cancel_cleanup():
            started.set()
            try:
                while True:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                raise

        stream_id, _stream_task = await owner.create(
            wait_forever_with_slow_cancel_cleanup(),
            metadata={"user_id": "user-1"},
        )
        await started.wait()

        result = await requester.stop(stream_id, requester_user_id="user-1")

        await owner.close()
        await bystander.close()
        await requester.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} successfully stopped.",
            "state": "cancelled",
        }

    asyncio.run(scenario())


def test_concurrent_remote_stops_do_not_cause_timeouts():
    """Each stop call gets its own request_id so no future is overwritten.
    One call succeeds; the second gets a clean not-found rather than a timeout."""

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

        async def wait_forever():
            started.set()
            while True:
                await asyncio.sleep(0)

        stream_id, _stream_task = await owner.create(
            wait_forever(),
            metadata={"user_id": "user-1"},
        )
        await started.wait()

        results = await asyncio.gather(
            requester.stop(stream_id, requester_user_id="user-1"),
            requester.stop(stream_id, requester_user_id="user-1"),
            return_exceptions=True,
        )

        await owner.close()
        await requester.close()

        # One call cancels the task; the other correctly gets a not-found response
        # because the task has already been removed. Neither should time out.
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, ValueError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0]["state"] == "cancelled"
        assert "not found" in str(failures[0]).lower()

    asyncio.run(scenario())


def test_stop_returns_failed_when_cancellation_cleanup_raises():
    async def scenario():
        manager = make_test_stream_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def fail_during_cancellation_cleanup():
            started.set()
            try:
                while True:
                    await asyncio.sleep(0)
            except asyncio.CancelledError as exc:
                raise RuntimeError("cleanup failed") from exc

        stream_id, _stream_task = await manager.create(
            fail_during_cancellation_cleanup(),
            metadata={"user_id": "user-1"},
        )
        await started.wait()

        result = await manager.stop(stream_id, requester_user_id="user-1")
        record = await manager.get(stream_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {stream_id} already failed.",
            "state": "failed",
        }
        assert record is None

    asyncio.run(scenario())
