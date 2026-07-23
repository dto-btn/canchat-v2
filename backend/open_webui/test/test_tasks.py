import asyncio

from open_webui.tasking import InMemoryTaskHub, TaskManager, TaskManagerSettings


def create_test_manager(
    instance_id: str,
    *,
    hub: InMemoryTaskHub | None = None,
    recent_task_ttl_seconds: int = 30,
    active_task_ttl_seconds: int = 60,
    remote_stop_wait_seconds: float = 5.0,
) -> TaskManager:
    shared_hub = hub or InMemoryTaskHub(
        active_task_ttl_seconds=active_task_ttl_seconds,
        recent_task_ttl_seconds=recent_task_ttl_seconds,
    )
    return TaskManager(
        backend=shared_hub.create_backend(),
        settings=TaskManagerSettings(
            instance_id=instance_id,
            recent_task_ttl_seconds=recent_task_ttl_seconds,
            remote_stop_wait_seconds=remote_stop_wait_seconds,
        ),
    )


def test_create_task_records_shared_running_state_before_task_starts():
    async def scenario():
        hub = InMemoryTaskHub(
            active_task_ttl_seconds=60,
            recent_task_ttl_seconds=30,
        )
        manager = create_test_manager(instance_id="owner", hub=hub)
        events = []

        original_save_record = manager.backend.save_record

        async def recording_save_record(record):
            events.append((record.state.value, record.task_id, dict(record.metadata)))
            return await original_save_record(record)

        manager.backend.save_record = recording_save_record
        await manager.start()

        async def complete_immediately():
            events.append(("started", None, None))

        task_id, task = await manager.create(
            complete_immediately(),
            metadata={"chat_id": "chat-id"},
        )
        await task
        await asyncio.sleep(0)
        await manager.close()

        assert events[0] == ("running", task_id, {"chat_id": "chat-id"})
        assert events[1] == ("started", None, None)

    asyncio.run(scenario())


def test_stop_task_returns_success_for_recently_completed_task():
    async def scenario():
        manager = create_test_manager(instance_id="owner")
        await manager.start()

        async def complete_immediately():
            return "done"

        task_id, task = await manager.create(complete_immediately())
        await task
        await asyncio.sleep(0)

        result = await manager.stop(task_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {task_id} already completed.",
            "state": "completed",
        }

    asyncio.run(scenario())


def test_stop_task_raises_for_unknown_task_id():
    async def scenario():
        manager = create_test_manager(instance_id="owner")
        await manager.start()

        try:
            await manager.stop("missing-task")
        except ValueError as exc:
            assert str(exc) == "Task with ID missing-task not found."
        else:
            raise AssertionError("Expected ValueError for missing task")
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_stop_task_uses_recent_terminal_cache_before_backend_lookup():
    async def scenario():
        manager = create_test_manager(instance_id="owner")
        await manager.start()
        manager.remember_task_state("recent-task", "cancelled")

        async def fail_load_record(task_id):
            raise AssertionError(f"backend load should not run for {task_id}")

        manager.backend.load_record = fail_load_record
        result = await manager.stop("recent-task")
        await manager.close()

        assert result == {
            "status": True,
            "message": "Task recent-task already cancelled.",
            "state": "cancelled",
        }

    asyncio.run(scenario())


def test_stop_task_marks_swallowed_cancellation_as_cancelled():
    async def scenario():
        manager = create_test_manager(instance_id="owner")
        await manager.start()
        started = asyncio.Event()

        async def swallow_cancelled_error():
            started.set()
            try:
                while True:
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                return "partial"

        task_id, _ = await manager.create(swallow_cancelled_error())
        await started.wait()

        result = await manager.stop(task_id)
        await manager.close()

        assert result == {
            "status": True,
            "message": f"Task {task_id} successfully stopped.",
            "state": "cancelled",
        }

    asyncio.run(scenario())


def test_remote_stop_is_event_driven_and_returns_terminal_state():
    async def scenario():
        hub = InMemoryTaskHub(
            active_task_ttl_seconds=60,
            recent_task_ttl_seconds=30,
        )
        owner = create_test_manager(instance_id="owner", hub=hub)
        requester = create_test_manager(instance_id="requester", hub=hub)
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

        task_id, _ = await owner.create(
            swallow_cancelled_error(),
            metadata={"chat_id": "chat-1"},
        )
        await started.wait()

        result = await requester.stop(task_id)
        await owner.close()
        await requester.close()

        assert result == {
            "status": True,
            "message": f"Task {task_id} successfully stopped.",
            "state": "cancelled",
        }

    asyncio.run(scenario())


def test_remote_terminal_state_is_cached_locally_after_stop():
    async def scenario():
        hub = InMemoryTaskHub(
            active_task_ttl_seconds=60,
            recent_task_ttl_seconds=30,
        )
        owner = create_test_manager(instance_id="owner", hub=hub)
        requester = create_test_manager(instance_id="requester", hub=hub)
        await owner.start()
        await requester.start()

        async def complete_immediately():
            return "done"

        task_id, task = await owner.create(complete_immediately())
        await task
        await asyncio.sleep(0)

        result = await requester.stop(task_id)
        cached_state = requester.get_recent_task_state(task_id)

        await owner.close()
        await requester.close()

        assert result == {
            "status": True,
            "message": f"Task {task_id} already completed.",
            "state": "completed",
        }
        assert cached_state == "completed"

    asyncio.run(scenario())
