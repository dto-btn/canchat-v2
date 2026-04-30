import asyncio
import json
import logging
import redis.asyncio as redis
import redis as redis_sync
import uuid

log = logging.getLogger(__name__)

RENEW_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""

RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


async def renew_lock_periodically(
    lock,
    renewal_interval_secs: int = 300,
    lock_name: str = "lock",
    on_lock_lost=None,
):
    """
    Periodically renew a Redis lock to keep it alive during long-running operations.

    Args:
        lock: RedisLock instance to renew
        renewal_interval_secs: Interval in seconds between renewal attempts (default: 300 = 5 minutes)
        lock_name: Name of the lock for logging purposes
        on_lock_lost: Optional callback invoked when lock renewal fails or the renewal task errors.

    This function runs in a loop until the lock is no longer held, renewal fails, or an
    unexpected renewal error occurs. On lock loss, it marks the lock as no longer held and
    triggers on_lock_lost when provided.
    It should be run as an asyncio task and cancelled when the operation completes.
    """
    try:
        while True:
            await asyncio.sleep(renewal_interval_secs)
            if lock and lock.lock_obtained:
                renewed = await lock.renew_lock()
                if renewed:
                    log.debug(f"Renewed {lock_name}")
                else:
                    log.warning(f"Failed to renew {lock_name}")
                    lock.lock_obtained = False
                    if on_lock_lost:
                        on_lock_lost()
                    break
    except asyncio.CancelledError:
        log.debug(f"Lock renewal task for {lock_name} cancelled")
        raise
    except Exception as e:
        log.error(f"Error in lock renewal task for {lock_name}: {e}")
        if lock:
            lock.lock_obtained = False
        if on_lock_lost:
            on_lock_lost()


class RedisLock:
    def __init__(self, redis_url, lock_name, timeout_secs):
        """Initialize a Redis-backed distributed lock instance."""
        self.lock_name = lock_name
        self.lock_id = str(uuid.uuid4())
        self.timeout_secs = timeout_secs
        self.lock_obtained = False
        self.last_error = None
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)

    async def acquire_lock(self):
        """Acquire lock key with NX semantics and TTL; returns True on success."""
        try:
            self.last_error = None
            # nx=True will only set this key if it _hasn't_ already been set
            self.lock_obtained = await self.redis.set(
                self.lock_name, self.lock_id, nx=True, ex=self.timeout_secs
            )
            return self.lock_obtained
        except Exception as e:
            self.last_error = e
            log.error(f"Error acquiring Redis lock: {e}")
            return False

    async def renew_lock(self):
        """
        Renew this lock's TTL only if the stored lock value still matches this instance's lock_id.

        Uses an atomic Redis Lua script (compare-and-expire) and returns True only when renewal
        succeeds for the current owner.
        """
        try:
            self.last_error = None
            # Atomically renew only if this process still owns the lock.
            renewed = await self.redis.eval(
                RENEW_LOCK_SCRIPT,
                1,
                self.lock_name,
                self.lock_id,
                str(self.timeout_secs),
            )
            return renewed == 1
        except Exception as e:
            self.last_error = e
            log.error(f"Error renewing Redis lock: {e}")
            return False

    async def release_lock(self):
        """
        Release this lock only if the current lock value still matches this instance's lock_id.

        Uses an atomic Redis Lua script (compare-and-delete) to avoid races between GET and DEL.
        Returns True when the lock key was deleted by this owner, False otherwise.
        """
        try:
            released = await self.redis.eval(
                RELEASE_LOCK_SCRIPT,
                1,
                self.lock_name,
                self.lock_id,
            )
            if released == 1:
                self.lock_obtained = False
                return True

            log.warning(
                "Redis lock release was not applied (lock already expired or ownership changed): %s",
                self.lock_name,
            )
            return False
        except Exception as e:
            log.error(f"Error releasing Redis lock: {e}")
            return False
        finally:
            self.lock_obtained = False


class RedisDict:
    def __init__(self, name, redis_url):
        self.name = name
        # Keep RedisDict synchronous because socket/main uses dict-like sync access.
        # This will be refactored in another PR to centralize Redis locking mechanism.
        self.redis = redis_sync.Redis.from_url(redis_url, decode_responses=True)

    def __setitem__(self, key, value):
        serialized_value = json.dumps(value)
        self.redis.hset(self.name, key, serialized_value)

    def __getitem__(self, key):
        value = self.redis.hget(self.name, key)
        if value is None:
            raise KeyError(key)
        return json.loads(value)

    def __delitem__(self, key):
        result = self.redis.hdel(self.name, key)
        if result == 0:
            raise KeyError(key)

    def __contains__(self, key):
        return self.redis.hexists(self.name, key)

    def __len__(self):
        return self.redis.hlen(self.name)

    def keys(self):
        return self.redis.hkeys(self.name)

    def values(self):
        return [json.loads(v) for v in self.redis.hvals(self.name)]

    def items(self):
        return [(k, json.loads(v)) for k, v in self.redis.hgetall(self.name).items()]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def clear(self):
        self.redis.delete(self.name)

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if hasattr(other, "items") else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]
