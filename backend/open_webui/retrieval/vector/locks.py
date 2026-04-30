"""
Distributed Locking for Vector Database Operations.

This module provides distributed locks using Redis to prevent race conditions
across concurrent operations in multiple workers/instances.

Design notes:
- Uses native async redis-py (`redis.asyncio`) to avoid threadpool overhead.
- Redis client lifecycle is event-loop aware to avoid cross-loop errors.
- Lua scripts are registered per client instance and re-registered on reconnect.
- Lock ownership is tracked per asyncio task for safe reentrancy.
"""

import asyncio
import inspect
import logging
import threading
import time
import uuid
import weakref
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Dict, Optional

import redis.asyncio as redis
from redis.exceptions import RedisError

from open_webui.env import REDIS_URL, SRC_LOG_LEVELS, USE_REDIS_LOCKS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


class CircuitState(Enum):
    """Circuit breaker states for Redis health.

    States:
    - CLOSED: Redis is healthy and lock operations proceed normally.
    - OPEN: Redis has experienced repeated failures (threshold reached); the circuit
      is opened to fail-fast lock operations and avoid overloading Redis. Lock
      calls should fail immediately until the recovery window elapses.
    - HALF_OPEN: After the open period, the manager transitions to HALF_OPEN to
      allow limited operations to test whether Redis has recovered. Successful
      checks close the circuit; further failures reopen it.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# Lua script for atomic compare-and-delete.
# Returns 1 if lock was deleted, 0 if lock was not owned by us.
RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua script for atomic compare-and-extend (for lease renewal).
# Returns 1 if TTL was extended, 0 if lock was not owned by us.
EXTEND_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class AsyncRedisLock:
    """Async Redis distributed lock with task-safe reentrancy and heartbeat renewal."""

    HEARTBEAT_INTERVAL = 10.0  # seconds (upper bound)

    def __init__(
        self,
        manager: "RedisCollectionLockManager",
        lock_name: str,
        timeout_secs: int = 30,
    ):
        self.manager = manager
        self.lock_name = f"lock:collection:{lock_name}"
        self.lock_id = str(uuid.uuid4())
        self.timeout_secs = timeout_secs
        # Renew at least once before expiration for all TTL values.
        self._heartbeat_interval = max(
            1.0, min(self.HEARTBEAT_INTERVAL, timeout_secs / 3)
        )

        # Task-safe ownership tracking: {task_id: reentry_count}
        self._owners: Dict[int, int] = {}
        self._owners_lock = threading.Lock()

        # Heartbeat task for renewal
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_stop: Optional[asyncio.Event] = None

        # Local fallback lock (used when Redis locks are disabled)
        self._local_lock: Optional[asyncio.Lock] = (
            asyncio.Lock() if not self.manager.use_redis_locks else None
        )

        # Track which client was used for script registration.
        self._last_registered_client = None
        self._release_script = None
        self._extend_script = None

        # Metrics for observability
        self._acquire_attempts = 0
        self._heartbeat_extensions = 0
        self._script_registration_time = time.time()

    @property
    def redis_client(self):
        """Access the manager's current Redis client (survives reconnections)."""
        return self.manager.redis_client

    def _ensure_scripts_registered(self):
        """Register Lua scripts on the current client if it has changed."""
        current_client = self.redis_client
        if current_client is None:
            return

        if self._last_registered_client is not current_client:
            log.info(
                f"[LOCK:SCRIPT_REG] Registering Lua scripts on new client: {self.lock_name}, "
                f"client_changed={self._last_registered_client is not None}"
            )
            self._release_script = current_client.register_script(RELEASE_LOCK_LUA)
            self._extend_script = current_client.register_script(EXTEND_LOCK_LUA)
            self._last_registered_client = current_client
            self._script_registration_time = time.time()

    def _get_task_id(self) -> int:
        """Get unique identifier for current asyncio task."""
        try:
            task = asyncio.current_task()
            return id(task) if task else id(threading.current_thread())
        except RuntimeError:
            return id(threading.current_thread())

    def _try_reentrant_acquire(self, task_id: Optional[int] = None) -> bool:
        """Return True if current task already owns the lock and increment the counter."""
        if task_id is None:
            task_id = self._get_task_id()
        with self._owners_lock:
            if task_id in self._owners:
                self._owners[task_id] += 1
                log.info(
                    f"[LOCK:REENTRY_DETECTED] Reentrant lock acquisition detected: {self.lock_name}, "
                    f"task={task_id}, new_count={self._owners[task_id]}"
                )
                return True
        return False

    async def _heartbeat_loop(self):
        """Background task that periodically extends the lock TTL."""
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._heartbeat_stop.wait(), timeout=self._heartbeat_interval
                    )
                    break
                except asyncio.TimeoutError:
                    pass

                try:
                    await self.manager._ensure_initialized()
                    self._ensure_scripts_registered()

                    result = await self._extend_script(
                        keys=[self.lock_name], args=[self.lock_id, self.timeout_secs]
                    )

                    if result == 0:
                        log.warning(
                            f"[LOCK:HEARTBEAT_LOST] Lock lost during heartbeat: {self.lock_name}. "
                            "Another process may have acquired it."
                        )
                        with self._owners_lock:
                            self._owners.clear()
                        break

                    self._heartbeat_extensions += 1
                    log.debug(
                        f"[LOCK:HEARTBEAT] Extended TTL for lock: {self.lock_name} "
                        f"(total: {self._heartbeat_extensions})"
                    )
                except Exception as e:
                    if isinstance(e, RedisError):
                        await self.manager._mark_client_unhealthy(
                            e, f"heartbeat:{self.lock_name}"
                        )
                    log.error(
                        f"[LOCK:HEARTBEAT_ERROR] Error extending lock TTL: {self.lock_name}, "
                        f"error={type(e).__name__}: {e}"
                    )
        except asyncio.CancelledError:
            log.debug(
                f"[LOCK:HEARTBEAT_CANCELLED] Heartbeat cancelled for lock: {self.lock_name}"
            )
        except Exception as e:
            log.error(
                f"[LOCK:HEARTBEAT_FATAL] Fatal error in heartbeat: {type(e).__name__}: {e}"
            )

    def _start_heartbeat(self):
        """Start heartbeat: create Event and schedule the loop on the running loop."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            try:
                # Bind Event/Task to the current loop; skip if none running.
                loop = asyncio.get_running_loop()
                self._heartbeat_stop = asyncio.Event()
                self._heartbeat_task = loop.create_task(self._heartbeat_loop())
            except RuntimeError:
                pass

    def _stop_heartbeat(self):
        """Stop heartbeat: set stop Event (graceful) and cancel Task (fallback)."""
        if self._heartbeat_stop is not None and not self._heartbeat_stop.is_set():
            # Signal graceful stop.
            self._heartbeat_stop.set()
        if self._heartbeat_task and not self._heartbeat_task.done():
            # Fallback: cancel running task.
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def acquire(
        self, blocking: bool = True, timeout: Optional[float] = None
    ) -> bool:
        """Acquire the distributed lock."""
        task_id = self._get_task_id()
        if self._try_reentrant_acquire(task_id):
            return True

        if not self.manager.use_redis_locks:
            local_lock = self._local_lock
            if local_lock is None:
                log.error(
                    f"[LOCK:LOCAL_ERROR] Local lock not initialized for {self.lock_name}"
                )
                return False

            try:
                if not blocking:
                    if local_lock.locked():
                        return False
                    await local_lock.acquire()
                elif timeout is not None:
                    await asyncio.wait_for(local_lock.acquire(), timeout=timeout)
                else:
                    await local_lock.acquire()

                with self._owners_lock:
                    self._owners[task_id] = 1

                log.debug(
                    f"[LOCK:ACQUIRED_LOCAL] In-process lock acquired: {self.lock_name}, "
                    f"task={task_id}"
                )
                return True
            except asyncio.TimeoutError:
                log.warning(
                    f"[LOCK:TIMEOUT_LOCAL] Local lock acquisition timeout: {self.lock_name}, "
                    f"task={task_id}, timeout={timeout}"
                )
                return False
            except Exception as e:
                log.error(
                    f"Exception acquiring local lock {self.lock_name}: {type(e).__name__}: {e}"
                )
                return False

        log.info(
            f"[LOCK:ACQUIRE_START] Starting lock acquisition: {self.lock_name}, task={task_id}"
        )
        start_time = time.time()
        attempt = 0

        while True:
            attempt += 1
            self._acquire_attempts += 1

            try:
                await self.manager._ensure_initialized()
                self._ensure_scripts_registered()

                client = self.redis_client
                if client is None:
                    log.error(f"Redis client is None for lock: {self.lock_name}")
                    return False

                acquired = await client.set(
                    self.lock_name,
                    self.lock_id,
                    nx=True,
                    ex=self.timeout_secs,
                )

                if acquired:
                    with self._owners_lock:
                        self._owners[task_id] = 1

                    self._start_heartbeat()

                    elapsed = time.time() - start_time
                    if elapsed > 1.0:
                        log.info(
                            f"[LOCK:ACQUIRED] Lock acquired after {attempt} attempts "
                            f"({elapsed:.2f}s): {self.lock_name}, task={task_id}"
                        )
                    else:
                        log.debug(
                            f"[LOCK:ACQUIRED] Lock acquired: {self.lock_name}, task={task_id}"
                        )
                    return True

                if not blocking:
                    return False

                if timeout is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        log.warning(
                            f"[LOCK:TIMEOUT] Lock acquisition timeout after {attempt} attempts "
                            f"({elapsed:.1f}s): {self.lock_name}"
                        )
                        return False

                await asyncio.sleep(0.05)

            except Exception as e:
                if isinstance(e, RedisError):
                    await self.manager._mark_client_unhealthy(
                        e, f"acquire:{self.lock_name}"
                    )
                log.error(
                    f"Exception acquiring Redis lock {self.lock_name}: {type(e).__name__}: {e}"
                )
                return False

    async def release(self) -> bool:
        """Release the distributed lock using atomic compare-and-delete Lua script."""
        task_id = self._get_task_id()

        with self._owners_lock:
            if task_id not in self._owners:
                log.warning(
                    f"[LOCK:RELEASE_NOT_OWNER] Attempted to release lock not owned by this task: "
                    f"{self.lock_name}, task={task_id}"
                )
                return False

            if self._owners[task_id] > 1:
                self._owners[task_id] -= 1
                log.debug(
                    f"[LOCK:REENTRY_RELEASE] Reentrant lock release: {self.lock_name}, "
                    f"task={task_id}, remaining_count={self._owners[task_id]}"
                )
                return True

            del self._owners[task_id]

        with self._owners_lock:
            if self._owners:
                log.debug(
                    f"[LOCK:RELEASE_PARTIAL] Lock still held by other tasks: {self.lock_name}, "
                    f"remaining_tasks={len(self._owners)}"
                )
                return True

        if not self.manager.use_redis_locks:
            local_lock = self._local_lock
            if local_lock is None:
                log.error(
                    f"[LOCK:LOCAL_ERROR] Local lock not initialized for {self.lock_name}"
                )
                return False

            if local_lock.locked():
                local_lock.release()

            log.debug(
                f"[LOCK:RELEASED_LOCAL] In-process lock released: {self.lock_name}, "
                f"task={task_id}"
            )
            return True

        self._stop_heartbeat()

        try:
            await self.manager._ensure_initialized()
            self._ensure_scripts_registered()

            result = await self._release_script(
                keys=[self.lock_name], args=[self.lock_id]
            )

            if result == 1:
                log.debug(
                    f"[LOCK:RELEASED] Lock released: {self.lock_name}, task={task_id}"
                )
                return True

            log.warning(
                f"[LOCK:RELEASE_EXPIRED] Lock expired or owned by another process: "
                f"{self.lock_name}. This may indicate the operation took too long."
            )
            return False

        except Exception as e:
            if isinstance(e, RedisError):
                await self.manager._mark_client_unhealthy(
                    e, f"release:{self.lock_name}"
                )
            log.error(
                f"Exception releasing Redis lock {self.lock_name}: {type(e).__name__}: {e}"
            )
            return False

    def is_owned_by_current_task(self) -> bool:
        """Check if the current task owns this lock."""
        task_id = self._get_task_id()
        with self._owners_lock:
            return task_id in self._owners

    def get_owner_count(self) -> int:
        """Get the number of tasks currently holding this lock."""
        with self._owners_lock:
            return len(self._owners)

    async def extend(self, additional_secs: Optional[int] = None) -> bool:
        """Manually extend the lock TTL."""
        if not self.is_owned_by_current_task():
            return False

        if not self.manager.use_redis_locks:
            return True

        try:
            ttl = additional_secs if additional_secs else self.timeout_secs
            await self.manager._ensure_initialized()
            self._ensure_scripts_registered()

            result = await self._extend_script(
                keys=[self.lock_name], args=[self.lock_id, ttl]
            )
            return result == 1
        except Exception as e:
            if isinstance(e, RedisError):
                await self.manager._mark_client_unhealthy(e, f"extend:{self.lock_name}")
            log.error(f"Error extending lock TTL: {type(e).__name__}: {e}")
            return False

    def get_metrics(self) -> Dict[str, Any]:
        """Get lock metrics for monitoring and debugging."""
        return {
            "lock_name": self.lock_name,
            "lock_id": self.lock_id,
            "timeout_secs": self.timeout_secs,
            "heartbeat_interval_secs": self._heartbeat_interval,
            "acquire_attempts": self._acquire_attempts,
            "heartbeat_extensions": self._heartbeat_extensions,
            "owner_count": self.get_owner_count(),
            "is_owned": self.is_owned_by_current_task(),
            "heartbeat_active": self._heartbeat_task is not None
            and not self._heartbeat_task.done(),
            "script_registration_age_secs": time.time()
            - self._script_registration_time,
        }

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
        return False


class RedisCollectionLockManager:
    """Singleton manager for distributed collection locks with Redis circuit breaker."""

    _instance: Optional["RedisCollectionLockManager"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __new__(cls, redis_url: Optional[str] = None) -> "RedisCollectionLockManager":
        """Enforce singleton pattern: return the same instance always."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance: RedisCollectionLockManager = super().__new__(cls)
                    instance.use_redis_locks = USE_REDIS_LOCKS
                    instance.redis_url = redis_url or REDIS_URL

                    if instance.use_redis_locks and not instance.redis_url:
                        raise RuntimeError(
                            "REDIS_URL environment variable is required for distributed locking. "
                            "Please set REDIS_URL to your Redis connection string (e.g., redis://localhost:6379/0)"
                        )

                    if (
                        instance.use_redis_locks
                        and instance.redis_url
                        and (
                            "localhost" in instance.redis_url
                            or "127.0.0.1" in instance.redis_url
                        )
                    ):
                        log.warning(
                            f"[LOCK:SECURITY] Using default localhost Redis ({instance.redis_url}). "
                            "For multi-instance deployments, ensure all instances share a central Redis."
                        )

                    if not instance.use_redis_locks:
                        log.warning(
                            "[LOCK:LOCAL_MODE] USE_REDIS_LOCKS=false; using in-process asyncio locks. "
                            "Distributed lock coordination is disabled for this process."
                        )

                    instance._initialized = False
                    instance.redis_client = None
                    instance._redis_loop_id = None
                    instance._redis_loop = None
                    instance._redis_clients_by_loop = {}
                    instance._redis_loops_by_id = {}
                    # NOTE: threading.Lock is intentional; protected sections only
                    # perform synchronous reference/state updates. Never add await
                    # calls while holding this lock.
                    instance._client_state_lock = threading.Lock()

                    # Lock cache: Map collection_name -> AsyncRedisLock.
                    instance._lock_cache = weakref.WeakValueDictionary()
                    instance._lock_cache_lock = threading.Lock()

                    instance._circuit_initialized = False
                    cls._instance = instance

        return cls._instance

    def __init__(self):
        """Initialize circuit breaker state and metrics once."""
        if getattr(self, "_circuit_initialized", False):
            return

        self._circuit_initialized = True
        self.circuit_state = CircuitState.CLOSED
        self.circuit_open_time: Optional[float] = None
        self.circuit_recovery_threshold = 30
        self.consecutive_failures = 0
        self.failure_threshold = 3

        # Metrics
        self.health_check_failures = 0
        self.acquire_timeouts = 0
        self.recovery_attempts = 0
        self.successful_recoveries = 0

    @staticmethod
    async def _maybe_await(value):
        """If `value` is awaitable, await and return its result; otherwise return it.

        This helper lets callers accept either sync results or coroutines/futures
        without duplicating sync/async branching.
        """
        if inspect.isawaitable(value):
            return await value
        return value

    async def _close_redis_client(self, client) -> None:
        """Close a redis client regardless of close/aclose implementation details."""
        if client is None:
            return

        try:
            close_method = getattr(client, "aclose", None)
            if close_method is not None:
                await self._maybe_await(close_method())
                return

            close_method = getattr(client, "close", None)
            if close_method is not None:
                await self._maybe_await(close_method())
        except Exception as e:
            log.debug(
                f"[LOCK:CLOSE_CLIENT_ERROR] Error closing Redis client: {type(e).__name__}: {e}"
            )

    def _close_client_in_loop(
        self, loop: Optional[asyncio.AbstractEventLoop], client
    ) -> bool:
        """
        Try to schedule Redis client close on its original loop.

        Returns True when close work was scheduled, False if caller should close inline.
        """
        if client is None or loop is None:
            return False

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if loop is current_loop:
            return False

        if loop.is_closed():
            log.debug(
                "[LOCK:CLOSE_CLIENT_SKIP] Skipping stale Redis client close: original loop is closed"
            )
            return True

        try:
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self._close_redis_client(client))
            )
            return True
        except Exception as e:
            log.debug(
                f"[LOCK:CLOSE_CLIENT_SCHEDULE_ERROR] Failed to schedule stale client close: "
                f"{type(e).__name__}: {e}"
            )
            return False

    def _client_ready_for_current_loop(self) -> bool:
        """Return True if Redis client exists and is bound to current loop."""
        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            return False

        with self._client_state_lock:
            client = self._redis_clients_by_loop.get(current_loop_id)
            if client is None:
                return False

            self.redis_client = client
            self._redis_loop_id = current_loop_id
            self._redis_loop = self._redis_loops_by_id.get(
                current_loop_id, current_loop
            )
            self._initialized = True
            return True

    async def _init_redis_client(self, retries: int = 1, delay: float = 0.0):
        """Initialize an async Redis client for the current event loop."""
        current_loop = asyncio.get_running_loop()
        current_loop_id = id(current_loop)

        with self._client_state_lock:
            existing_client = self._redis_clients_by_loop.get(current_loop_id)
            if existing_client is not None:
                self.redis_client = existing_client
                self._redis_loop_id = current_loop_id
                self._redis_loop = current_loop
                self._initialized = True
                return

        for attempt in range(retries):
            candidate_client = None
            try:
                candidate_client = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=False,
                    socket_connect_timeout=2,
                    socket_keepalive=True,
                    health_check_interval=30,
                )
                await candidate_client.ping()

                with self._client_state_lock:
                    self._redis_clients_by_loop[current_loop_id] = candidate_client
                    self._redis_loops_by_id[current_loop_id] = current_loop
                    self.redis_client = candidate_client
                    self._redis_loop_id = current_loop_id
                    self._redis_loop = current_loop
                    self._initialized = True

                log.info(
                    f"[LOCK:INIT] Redis lock manager initialized successfully: url={self.redis_url}, "
                    f"loop_id={current_loop_id}"
                )
                return

            except Exception as e:
                if candidate_client is not None:
                    await self._close_redis_client(candidate_client)

                if attempt < retries - 1:
                    await asyncio.sleep(delay)
                else:
                    log.debug(f"[LOCK:INIT_PENDING] Redis not ready yet: {e}")
                    with self._client_state_lock:
                        self._initialized = bool(self._redis_clients_by_loop)

    async def _mark_client_unhealthy(self, error: Exception, operation: str) -> None:
        """Mark current Redis client unhealthy and close it to force re-initialization."""
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None

        stale_client = None
        stale_loop = None
        with self._client_state_lock:
            if current_loop_id is not None:
                stale_client = self._redis_clients_by_loop.pop(current_loop_id, None)
                stale_loop = self._redis_loops_by_id.pop(current_loop_id, None)

            if stale_client is not None and self.redis_client is stale_client:
                if self._redis_clients_by_loop:
                    fallback_loop_id, fallback_client = next(
                        iter(self._redis_clients_by_loop.items())
                    )
                    self.redis_client = fallback_client
                    self._redis_loop_id = fallback_loop_id
                    self._redis_loop = self._redis_loops_by_id.get(fallback_loop_id)
                else:
                    self.redis_client = None
                    self._redis_loop_id = None
                    self._redis_loop = None

            self._initialized = bool(self._redis_clients_by_loop)

        if not self._close_client_in_loop(stale_loop, stale_client):
            await self._close_redis_client(stale_client)
        log.warning(
            f"[LOCK:CLIENT_UNHEALTHY] Marked Redis client unhealthy after {operation}: "
            f"{type(error).__name__}: {error}"
        )

    async def _prune_closed_loops(self) -> None:
        """Remove dict entries for event loops that have since closed.

        Called from _ensure_initialized() only when the current loop has no
        client yet (the slow path), so the iteration cost is amortized across
        the infrequent new-loop events rather than every lock acquisition.
        """
        stale = []
        with self._client_state_lock:
            for loop_id, loop in list(self._redis_loops_by_id.items()):
                if loop.is_closed():
                    client = self._redis_clients_by_loop.pop(loop_id, None)
                    self._redis_loops_by_id.pop(loop_id, None)
                    stale.append((loop, client))
            remaining = len(self._redis_clients_by_loop)

        if not stale:
            return

        log.info(
            f"[LOCK:PRUNE] Pruned {len(stale)} stale Redis client(s) for closed loops, "
            f"remaining_active={remaining}"
        )
        for loop, client in stale:
            if not self._close_client_in_loop(loop, client):
                await self._close_redis_client(client)

    async def _ensure_initialized(self):
        """Ensure Redis client is initialized and loop-compatible."""
        if not self.use_redis_locks:
            return

        if self._client_ready_for_current_loop():
            return

        # Remove stale closed-loop entries before potentially adding a new one.
        await self._prune_closed_loops()

        with self._client_state_lock:
            active_clients = len(self._redis_clients_by_loop)

        if active_clients > 0:
            log.info(
                f"[LOCK:RECOVER] Redis client loop mismatch (active_clients={active_clients}), "
                "initializing client for current loop..."
            )
        else:
            log.warning(
                "[LOCK:RECOVER] Redis client missing or uninitialized, attempting to reconnect..."
            )

        await self._init_redis_client(retries=1)

        if not self._client_ready_for_current_loop():
            raise RuntimeError("Redis lock manager not initialized and recovery failed")

    def _create_lock(
        self,
        collection_name: str,
        timeout_secs: int = 30,
    ) -> AsyncRedisLock:
        """Get or create a cached lock instance for a collection."""
        log.info(
            f"[LOCK:_CREATE_LOCK_CALLED] collection={collection_name}, timeout={timeout_secs}s"
        )
        with self._lock_cache_lock:
            lock = self._lock_cache.get(collection_name)

            if lock is None:
                lock = AsyncRedisLock(self, collection_name, timeout_secs)
                self._lock_cache[collection_name] = lock
                log.info(
                    f"[LOCK:CACHE_MISS] Created new cached lock for collection: {collection_name}, "
                    f"timeout={timeout_secs}s"
                )
            else:
                log.info(
                    f"[LOCK:CACHE_HIT] Reusing cached lock for collection: {collection_name}"
                )
            return lock

    @asynccontextmanager
    async def acquire_lock(self, collection_name: str, timeout_secs: int = 30):
        """Acquire a collection lock with circuit breaker guardrails."""
        if self.use_redis_locks and self._should_fail_fast():
            log.error(
                f"[LOCK:CIRCUIT_OPEN] Failing fast: circuit is OPEN for collection {collection_name}. "
                "Redis appears to be persistently unavailable."
            )
            raise RuntimeError(
                f"Redis circuit breaker is OPEN. Distribution locking unavailable for {collection_name}. "
                f"Waiting {self.circuit_recovery_threshold}s before retry."
            )

        await self._ensure_initialized()

        log.info(
            f"[LOCK:CONTEXT_ENTER] Entering lock context manager: "
            f"collection={collection_name}, timeout={timeout_secs}s"
        )

        lock = self._create_lock(collection_name, timeout_secs)

        if lock._try_reentrant_acquire():
            log.info(
                f"[LOCK:CONTEXT_REENTER] Reentering lock context manager: "
                f"collection={collection_name}, lock_id={id(lock)}"
            )
            acquired = True
        else:
            try:
                acquired = await lock.acquire(blocking=True, timeout=timeout_secs * 2)
            except asyncio.TimeoutError:
                self.acquire_timeouts += 1
                log.error(
                    f"[LOCK:ACQUIRE_TIMEOUT] Lock acquisition timeout for {collection_name} "
                    f"(total timeouts: {self.acquire_timeouts})"
                )
                raise

        if not acquired:
            log.error(
                f"[LOCK:CONTEXT_ERROR] Failed to acquire lock for collection: "
                f"{collection_name}, timeout={timeout_secs}s"
            )
            raise TimeoutError(
                f"Failed to acquire lock for collection: {collection_name}"
            )

        log.debug(
            f"[LOCK:CONTEXT_ACTIVE] Lock acquired and context active: "
            f"collection={collection_name}"
        )
        try:
            yield lock
        finally:
            try:
                await lock.release()
                log.debug(
                    f"[LOCK:CONTEXT_EXIT] Exiting lock context manager: "
                    f"collection={collection_name}"
                )
            except Exception as e:
                log.error(
                    f"[LOCK:CONTEXT_ERROR] Error releasing lock in context manager: "
                    f"collection={collection_name}, error={type(e).__name__}: {e}"
                )

    async def health_check(self, retries: int = 5) -> bool:
        """Comprehensive Redis health check with retries and functional validation."""
        if not self.use_redis_locks:
            return True

        for attempt in range(retries):
            try:
                if not self._client_ready_for_current_loop():
                    await self._init_redis_client(retries=1)

                if not self._client_ready_for_current_loop() or not self.redis_client:
                    raise RuntimeError("Redis client is not ready after initialization")

                check_key = f"lock:health_check:{uuid.uuid4()}"
                await self.redis_client.set(check_key, "ok", ex=10)
                val = await self.redis_client.get(check_key)
                await self.redis_client.delete(check_key)

                if val != b"ok":
                    raise RuntimeError(
                        f"Redis functional test failed. Expected b'ok', got: {val!r}"
                    )

                self.consecutive_failures = 0

                if self.circuit_state in (
                    CircuitState.OPEN,
                    CircuitState.HALF_OPEN,
                ):
                    self.successful_recoveries += 1
                    log.info(
                        "[LOCK:CIRCUIT_CLOSED] Redis recovered. "
                        f"Successful recoveries: {self.successful_recoveries}"
                    )

                self.circuit_state = CircuitState.CLOSED
                self.circuit_open_time = None
                log.info(
                    f"[LOCK:HEALTH_OK] Redis validated successfully: {self.redis_url}"
                )
                return True

            except Exception as e:
                self.consecutive_failures += 1
                await self._mark_client_unhealthy(e, "health_check")
                log.warning(
                    f"[LOCK:HEALTH_RETRY] Redis health check attempt {attempt + 1}/{retries} failed "
                    f"(consecutive failures: {self.consecutive_failures}): {type(e).__name__}: {e}"
                )

            if attempt < retries - 1:
                wait_time = min(2**attempt, 5)
                await asyncio.sleep(wait_time)

        self.health_check_failures += 1

        if self.consecutive_failures >= self.failure_threshold:
            if self.circuit_state == CircuitState.CLOSED:
                log.error(
                    f"[LOCK:CIRCUIT_OPEN] Redis health check failed {self.consecutive_failures} times. "
                    f"Opening circuit for {self.circuit_recovery_threshold}s."
                )
                self.circuit_state = CircuitState.OPEN
                self.circuit_open_time = time.time()
            elif self.circuit_state == CircuitState.HALF_OPEN:
                log.error(
                    "[LOCK:CIRCUIT_OPEN_AGAIN] Recovery test failed, reopening circuit."
                )
                self.circuit_state = CircuitState.OPEN
                self.circuit_open_time = time.time()

        log.error(
            f"[LOCK:HEALTH_FAIL] Redis health check failed after {retries} attempts. "
            f"Circuit state: {self.circuit_state.value}"
        )
        return False

    def _should_fail_fast(self) -> bool:
        """Return True if circuit breaker should block lock operations."""
        if self.circuit_state == CircuitState.CLOSED:
            return False

        if self.circuit_state == CircuitState.OPEN:
            if self.circuit_open_time is None:
                return True

            elapsed = time.time() - self.circuit_open_time
            if elapsed > self.circuit_recovery_threshold:
                log.info(
                    f"[LOCK:CIRCUIT_HALF_OPEN] {elapsed:.1f}s elapsed. "
                    "Transitioning to HALF_OPEN to test recovery."
                )
                self.circuit_state = CircuitState.HALF_OPEN
                self.recovery_attempts += 1
                return False
            return True

        return False

    def get_health_metrics(self) -> Dict[str, Any]:
        """Return health metrics for monitoring and readiness checks."""
        with self._client_state_lock:
            active_clients = len(self._redis_clients_by_loop)

        return {
            "use_redis_locks": self.use_redis_locks,
            "circuit_state": self.circuit_state.value,
            "consecutive_failures": self.consecutive_failures,
            "health_check_failures": self.health_check_failures,
            "acquire_timeouts": self.acquire_timeouts,
            "recovery_attempts": self.recovery_attempts,
            "successful_recoveries": self.successful_recoveries,
            "redis_initialized": self._initialized,
            "redis_url": self.redis_url if self.redis_url else "NOT_SET",
            "redis_loop_id": self._redis_loop_id,
            "redis_active_clients": active_clients,
        }

    async def close(self):
        """Close Redis connection and cleanup active lock heartbeats."""
        with self._lock_cache_lock:
            locks = list(self._lock_cache.values())

        for lock in locks:
            lock._stop_heartbeat()

        if not self.use_redis_locks:
            return

        clients_to_close = []
        with self._client_state_lock:
            for loop_id, client in self._redis_clients_by_loop.items():
                clients_to_close.append((self._redis_loops_by_id.get(loop_id), client))

            self._redis_clients_by_loop.clear()
            self._redis_loops_by_id.clear()
            self.redis_client = None
            self._redis_loop_id = None
            self._redis_loop = None
            self._initialized = False

        seen = set()
        for loop, client in clients_to_close:
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            if not self._close_client_in_loop(loop, client):
                await self._close_redis_client(client)


# Global singleton instance - managed by RedisCollectionLockManager.__new__
_redis_collection_lock_manager: Optional[RedisCollectionLockManager] = None


def get_collection_lock_manager() -> RedisCollectionLockManager:
    """Get the global Redis collection lock manager singleton."""
    global _redis_collection_lock_manager

    if _redis_collection_lock_manager is None:
        _redis_collection_lock_manager = RedisCollectionLockManager()

    return _redis_collection_lock_manager
