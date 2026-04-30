import asyncio
import sys
import types

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from open_webui.socket.utils import RedisLock


class FakeRedis:
    def __init__(self, current_value):
        self.current_value = current_value
        self.ttl_was_extended = False
        self.eval_calls = []
        self.delete_called = False

    async def set(self, *args, **kwargs):
        return True

    async def get(self, key):
        return self.current_value

    async def eval(self, script, numkeys, key, lock_id, timeout_secs=None):
        self.eval_calls.append(script)
        if "EXPIRE" in script:
            if self.current_value == lock_id:
                self.ttl_was_extended = True
                return 1
            return 0

        if "DEL" in script:
            if self.current_value == lock_id:
                self.current_value = None
                return 1
            return 0

        return 0

    async def delete(self, key):
        self.delete_called = True
        return 1


def test_renew_lock_does_not_extend_ttl_for_foreign_owner(monkeypatch):
    """
    Safety expectation for distributed locks:
    if another instance owns the lock, this instance must not touch expiry.
    """
    fake_redis = FakeRedis(current_value="another-instance-lock-id")

    class FakeRedisFactory:
        @staticmethod
        def from_url(url, decode_responses=True):
            return fake_redis

    monkeypatch.setattr("open_webui.socket.utils.redis.Redis", FakeRedisFactory)

    lock = RedisLock("redis://test", "chat_cleanup_job", 1800)
    lock.lock_id = "this-instance-lock-id"

    renewed = asyncio.run(lock.renew_lock())

    assert renewed is False
    assert fake_redis.ttl_was_extended is False


def test_release_lock_uses_atomic_compare_and_delete(monkeypatch):
    """
    Lock release should use atomic compare-and-delete script instead of separate GET/DELETE calls.
    """
    fake_redis = FakeRedis(current_value="this-instance-lock-id")

    class FakeRedisFactory:
        @staticmethod
        def from_url(url, decode_responses=True):
            return fake_redis

    monkeypatch.setattr("open_webui.socket.utils.redis.Redis", FakeRedisFactory)

    lock = RedisLock("redis://test", "chat_cleanup_job", 1800)
    lock.lock_id = "this-instance-lock-id"
    released = asyncio.run(lock.release_lock())

    assert released is True
    assert any("DEL" in script for script in fake_redis.eval_calls)
    assert fake_redis.delete_called is False
