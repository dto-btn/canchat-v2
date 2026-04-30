"""Reliability suite for Redis lock manager behavior under failure/recovery paths.

Covers singleton guarantees, reentrancy, circuit-breaker transitions, health metrics,
fast-fail behavior, and heartbeat lifecycle semantics.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import open_webui.retrieval.vector.locks as locks_module
from open_webui.retrieval.vector.locks import (
    AsyncRedisLock,
    CircuitState,
    RedisCollectionLockManager,
    get_collection_lock_manager,
)

# Constants for testing
TEST_COLLECTION = "test-reliability-collection"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_manager_singleton():
    """Reset singleton state before each test to ensure isolation."""
    # pylint: disable=protected-access
    previous_use_redis_locks = locks_module.USE_REDIS_LOCKS
    RedisCollectionLockManager._instance = None
    locks_module._redis_collection_lock_manager = None
    yield
    locks_module.USE_REDIS_LOCKS = previous_use_redis_locks
    RedisCollectionLockManager._instance = None
    locks_module._redis_collection_lock_manager = None


@pytest.fixture
def manager(monkeypatch):
    """Get a fresh manager instance with an async-capable mocked redis client."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", True)
    m = get_collection_lock_manager()

    m.redis_client = MagicMock()
    m.redis_client.set = AsyncMock(return_value=True)
    m.redis_client.get = AsyncMock(return_value=b"ok")
    m.redis_client.delete = AsyncMock(return_value=1)
    m.redis_client.ping = AsyncMock(return_value=True)

    # Return async script callables for release/extend.
    m.redis_client.register_script = MagicMock(
        side_effect=[AsyncMock(return_value=1), AsyncMock(return_value=1)]
    )

    m._initialized = True
    m._redis_loop_id = 1
    m._client_ready_for_current_loop = MagicMock(return_value=True)
    return m


@pytest.mark.anyio
async def test_singleton_enforcement():
    """Verify that multiple calls to get_collection_lock_manager return the same instance."""
    m1 = get_collection_lock_manager()
    m2 = get_collection_lock_manager()
    assert m1 is m2
    assert m1.circuit_state == CircuitState.CLOSED


@pytest.mark.anyio
async def test_lock_reentrancy_same_task(manager):
    """Test that the same task can acquire the same lock multiple times."""
    with patch.object(manager, "_ensure_initialized", new=AsyncMock(return_value=None)):
        async with manager.acquire_lock(TEST_COLLECTION) as lock1:
            assert lock1.get_owner_count() == 1
            task_id = lock1._get_task_id()
            assert lock1._owners[task_id] == 1

            async with manager.acquire_lock(TEST_COLLECTION) as lock2:
                assert lock1 is lock2
                assert lock1.get_owner_count() == 1
                assert lock1._owners[task_id] == 2

            assert lock1._owners[task_id] == 1

        assert lock1.get_owner_count() == 0


@pytest.mark.anyio
async def test_circuit_breaker_transition_open(manager):
    """Test CLOSED -> OPEN transition after repeated failures."""
    manager.failure_threshold = 2

    manager._client_ready_for_current_loop = MagicMock(return_value=False)
    manager._init_redis_client = AsyncMock(side_effect=Exception("Redis Down"))

    # 1st failure
    await manager.health_check(retries=1)
    assert manager.circuit_state == CircuitState.CLOSED
    assert manager.consecutive_failures == 1

    # 2nd failure -> trigger OPEN
    await manager.health_check(retries=1)
    assert manager.circuit_state == CircuitState.OPEN
    assert manager.consecutive_failures == 2
    assert manager._should_fail_fast() is True


@pytest.mark.anyio
async def test_circuit_breaker_recovery_half_open(manager):
    """Test OPEN -> HALF_OPEN after recovery threshold."""
    manager.circuit_state = CircuitState.OPEN
    manager.circuit_open_time = time.time() - 40
    manager.circuit_recovery_threshold = 30

    assert manager._should_fail_fast() is False
    assert manager.circuit_state == CircuitState.HALF_OPEN


@pytest.mark.anyio
async def test_circuit_breaker_recovery_success(manager):
    """Test HALF_OPEN -> CLOSED on successful health check."""
    manager.circuit_state = CircuitState.HALF_OPEN
    manager.consecutive_failures = 5
    manager._client_ready_for_current_loop = MagicMock(return_value=True)

    success = await manager.health_check(retries=1)
    assert success is True
    assert manager.circuit_state == CircuitState.CLOSED
    assert manager.consecutive_failures == 0
    assert manager.successful_recoveries == 1


@pytest.mark.anyio
async def test_acquire_lock_fast_fail_when_open(manager):
    """Verify that acquire_lock raises an error immediately if circuit is OPEN."""
    manager.circuit_state = CircuitState.OPEN
    manager.circuit_open_time = time.time()

    with pytest.raises(RuntimeError) as excinfo:
        async with manager.acquire_lock(TEST_COLLECTION):
            pass
    assert "Redis circuit breaker is OPEN" in str(excinfo.value)


@pytest.mark.anyio
async def test_health_metrics(manager):
    """Verify metrics report correctly."""
    manager.health_check_failures = 10
    manager.successful_recoveries = 2
    manager.circuit_state = CircuitState.OPEN

    metrics = manager.get_health_metrics()
    assert metrics["health_check_failures"] == 10
    assert metrics["successful_recoveries"] == 2
    assert metrics["circuit_state"] == "open"


@pytest.mark.anyio
async def test_lock_heartbeat_mechanism(manager):
    """Test that AsyncRedisLock starts/stops heartbeat correctly."""
    release_script = AsyncMock(return_value=1)
    extend_script = AsyncMock(return_value=1)
    manager.redis_client.register_script = MagicMock(
        side_effect=[release_script, extend_script]
    )

    lock = AsyncRedisLock(manager, "heartbeat-test")

    with patch.object(manager, "_ensure_initialized", new=AsyncMock(return_value=None)):
        await lock.acquire()
        assert lock._heartbeat_task is not None
        assert not lock._heartbeat_task.done()

        await lock.release()

        await asyncio.sleep(0.05)
        assert lock._heartbeat_task is None


@pytest.mark.anyio
async def test_local_mode_acquires_without_redis_init(monkeypatch):
    """When USE_REDIS_LOCKS is false, lock acquisition should work without Redis initialization."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", False)
    manager = get_collection_lock_manager()
    manager._init_redis_client = AsyncMock(
        side_effect=RuntimeError("should not be called")
    )

    async with manager.acquire_lock("local-mode-collection") as lock:
        assert manager.use_redis_locks is False
        assert lock.is_owned_by_current_task() is True
        assert lock._heartbeat_task is None

    manager._init_redis_client.assert_not_called()


@pytest.mark.anyio
async def test_local_mode_serializes_concurrent_tasks(monkeypatch):
    """Local asyncio fallback should still serialize concurrent tasks within one process."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", False)
    manager = get_collection_lock_manager()

    active = 0
    max_active = 0
    guard = asyncio.Lock()

    async def _worker():
        nonlocal active, max_active
        async with manager.acquire_lock("local-serialize"):
            async with guard:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            async with guard:
                active -= 1

    await asyncio.gather(_worker(), _worker())

    assert max_active == 1


@pytest.mark.anyio
async def test_local_mode_health_check_reports_healthy(monkeypatch):
    """Local mode should report health check as healthy without Redis."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", False)
    manager = get_collection_lock_manager()

    assert await manager.health_check(retries=1) is True
    metrics = manager.get_health_metrics()
    assert metrics["use_redis_locks"] is False


@pytest.mark.anyio
async def test_local_mode_extend_is_noop(monkeypatch):
    """Extending a local lock should succeed immediately without Redis interaction."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", False)
    manager = get_collection_lock_manager()

    async with manager.acquire_lock("local-extend-test") as lock:
        result = await lock.extend(additional_secs=60)
        assert result is True
        # Heartbeat was never started (no-op in local mode)
        assert lock._heartbeat_task is None


@pytest.mark.anyio
async def test_local_mode_close_does_not_raise(monkeypatch):
    """Closing the manager in local mode should be a no-op with no Redis teardown."""
    monkeypatch.setattr(locks_module, "USE_REDIS_LOCKS", False)
    manager = get_collection_lock_manager()

    # Acquire and release a lock so there is something in the lock cache
    async with manager.acquire_lock("local-close-test"):
        pass

    # Close should not raise and should leave the manager in a usable state
    await manager.close()
    assert manager.redis_client is None
    assert manager._initialized is False


@pytest.mark.anyio
async def test_prune_closed_loops_removes_only_stale_entries(manager):
    """Closed-loop Redis clients should be evicted while active-loop entries remain."""
    stale_loop = MagicMock()
    stale_loop.is_closed.return_value = True
    active_loop = MagicMock()
    active_loop.is_closed.return_value = False

    stale_client = MagicMock()
    active_client = MagicMock()

    manager._redis_clients_by_loop = {1: stale_client, 2: active_client}
    manager._redis_loops_by_id = {1: stale_loop, 2: active_loop}
    manager._close_client_in_loop = MagicMock(return_value=False)
    manager._close_redis_client = AsyncMock(return_value=None)

    await manager._prune_closed_loops()

    assert manager._redis_clients_by_loop == {2: active_client}
    assert manager._redis_loops_by_id == {2: active_loop}
    manager._close_client_in_loop.assert_called_once_with(stale_loop, stale_client)
    manager._close_redis_client.assert_awaited_once_with(stale_client)


@pytest.mark.anyio
async def test_ensure_initialized_prunes_before_reinit(manager):
    """Slow-path initialization should prune stale loop entries before creating a client."""
    manager._client_ready_for_current_loop = MagicMock(side_effect=[False, True])
    manager._prune_closed_loops = AsyncMock(return_value=None)
    manager._init_redis_client = AsyncMock(return_value=None)

    await manager._ensure_initialized()

    manager._prune_closed_loops.assert_awaited_once()
    manager._init_redis_client.assert_awaited_once_with(retries=1)


@pytest.mark.anyio
async def test_ensure_initialized_skips_prune_when_loop_is_ready(manager):
    """Fast path should return immediately when current loop client is already ready."""
    manager._client_ready_for_current_loop = MagicMock(return_value=True)
    manager._prune_closed_loops = AsyncMock(return_value=None)
    manager._init_redis_client = AsyncMock(return_value=None)

    await manager._ensure_initialized()

    manager._prune_closed_loops.assert_not_awaited()
    manager._init_redis_client.assert_not_called()
