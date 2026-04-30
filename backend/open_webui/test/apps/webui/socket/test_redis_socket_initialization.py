import sys
import types

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))


class FakeAsyncRedisManager:
    def __init__(self, url):
        self.url = url


class FakeAsyncServer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.manager = kwargs.get("client_manager")


class FakeRedisDict(dict):
    failures_before_success = 0
    ping_calls = 0

    def __init__(self, name, redis_url):
        super().__init__()
        self.name = name
        self.redis_url = redis_url
        self.redis = self

    def ping(self):
        type(self).ping_calls += 1
        if type(self).ping_calls <= type(self).failures_before_success:
            raise RuntimeError("redis unavailable")
        return True


class FakeLock:
    instances = []

    def __init__(self, redis_url, lock_name, timeout_secs):
        self.redis_url = redis_url
        self.lock_name = lock_name
        self.timeout_secs = timeout_secs
        type(self).instances.append(self)

    async def acquire_lock(self):
        return True

    async def renew_lock(self):
        return True

    async def release_lock(self):
        return True


def test_initialize_socket_state_retries_then_uses_redis(monkeypatch):
    from open_webui.socket import main as socket_main

    FakeRedisDict.failures_before_success = 1
    FakeRedisDict.ping_calls = 0
    FakeLock.instances = []
    sleep_calls = []

    monkeypatch.setattr(socket_main, "WEBSOCKET_MANAGER", "redis")
    monkeypatch.setattr(socket_main, "WEBSOCKET_REDIS_URL", "redis://test")
    monkeypatch.setattr(socket_main, "RedisDict", FakeRedisDict)
    monkeypatch.setattr(socket_main, "RedisLock", FakeLock)
    monkeypatch.setattr(
        socket_main.socketio, "AsyncRedisManager", FakeAsyncRedisManager
    )
    monkeypatch.setattr(socket_main.socketio, "AsyncServer", FakeAsyncServer)
    monkeypatch.setattr(
        socket_main.time, "sleep", lambda delay: sleep_calls.append(delay)
    )

    (
        effective_manager,
        sio,
        session_pool,
        user_pool,
        usage_pool,
        acquire_func,
        renew_func,
        release_func,
    ) = socket_main._initialize_socket_state()

    assert effective_manager == "redis"
    assert isinstance(sio.manager, FakeAsyncRedisManager)
    assert isinstance(session_pool, FakeRedisDict)
    assert isinstance(user_pool, FakeRedisDict)
    assert isinstance(usage_pool, FakeRedisDict)
    assert sleep_calls == [socket_main.TIMEOUT_DURATION]
    assert acquire_func.__self__ is FakeLock.instances[0]
    assert renew_func.__self__ is FakeLock.instances[0]
    assert release_func.__self__ is FakeLock.instances[0]


def test_initialize_socket_state_falls_back_to_local_consistently(monkeypatch):
    from open_webui.socket import main as socket_main

    FakeRedisDict.failures_before_success = 10
    FakeRedisDict.ping_calls = 0
    sleep_calls = []

    monkeypatch.setattr(socket_main, "WEBSOCKET_MANAGER", "redis")
    monkeypatch.setattr(socket_main, "WEBSOCKET_REDIS_URL", "redis://test")
    monkeypatch.setattr(socket_main, "RedisDict", FakeRedisDict)
    monkeypatch.setattr(socket_main, "RedisLock", FakeLock)
    monkeypatch.setattr(
        socket_main.socketio, "AsyncRedisManager", FakeAsyncRedisManager
    )
    monkeypatch.setattr(socket_main.socketio, "AsyncServer", FakeAsyncServer)
    monkeypatch.setattr(
        socket_main.time, "sleep", lambda delay: sleep_calls.append(delay)
    )

    (
        effective_manager,
        sio,
        session_pool,
        user_pool,
        usage_pool,
        acquire_func,
        renew_func,
        release_func,
    ) = socket_main._initialize_socket_state()

    assert effective_manager == "local"
    assert sio.manager is None
    assert session_pool == {}
    assert user_pool == {}
    assert usage_pool == {}
    assert acquire_func is socket_main._lock_noop
    assert renew_func is socket_main._lock_noop
    assert release_func is socket_main._lock_noop
    assert sleep_calls == [socket_main.TIMEOUT_DURATION] * 4
