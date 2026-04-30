import asyncio
import sys
import types
from datetime import datetime, timezone, timedelta

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from open_webui import scheduler


class ConfigValue:
    def __init__(self, value):
        self.value = value


class FakeLock:
    acquire_result = True
    acquire_error = None

    def __init__(self, redis_url, lock_name, timeout_secs):
        self.redis_url = redis_url
        self.lock_name = lock_name
        self.timeout_secs = timeout_secs
        self.lock_obtained = False
        self.release_called = False
        self.last_error = None

    async def acquire_lock(self):
        self.last_error = self.acquire_error
        self.lock_obtained = self.acquire_result
        return self.acquire_result

    async def release_lock(self):
        self.release_called = True
        return True


async def _no_op_lock_renewal(*args, **kwargs):
    """Placeholder lock-renewal coroutine for scheduler tests."""
    await asyncio.sleep(0)


def _patch_common_scheduler_dependencies(
    monkeypatch, lock_acquired=True, lock_error=None
):
    """Patch scheduler module globals to deterministic test-safe values."""
    FakeLock.acquire_result = lock_acquired
    FakeLock.acquire_error = lock_error

    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_ENABLED", ConfigValue(True))
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_DAYS", ConfigValue(30))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_PRESERVE_PINNED", ConfigValue(True))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_PRESERVE_ARCHIVED", ConfigValue(False))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_LOCK_TIMEOUT", 1800)
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_LOCK_RENEWAL_INTERVAL", 300)
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_CRON", "0 2 * * *")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_TIMEZONE", "America/Toronto")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULER_MISFIRE_GRACE_SECONDS", 300)
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_ALLOW_LOCAL_NO_REDIS", False)
    monkeypatch.setattr(scheduler, "USE_REDIS_LOCKS", True)
    monkeypatch.setattr(scheduler, "REDIS_URL", "redis://test")
    monkeypatch.setattr(scheduler, "RedisLock", FakeLock)
    monkeypatch.setattr(scheduler, "renew_lock_periodically", _no_op_lock_renewal)


def _install_fake_retrieval_module(monkeypatch, cleanup_fn):
    """Inject a lightweight retrieval module exposing cleanup_expired_chats_streaming."""
    module = types.ModuleType("open_webui.routers.retrieval")
    module.cleanup_expired_chats_streaming = cleanup_fn
    monkeypatch.setitem(sys.modules, "open_webui.routers.retrieval", module)


def test_automated_cleanup_runs_once_when_lock_acquired(monkeypatch):
    """
    Scheduler should execute cleanup once when the distributed lock is acquired.
    """
    _patch_common_scheduler_dependencies(monkeypatch, lock_acquired=True)

    calls = {"cleanup": 0}

    async def fake_cleanup(*args, **kwargs):
        calls["cleanup"] += 1
        return {"ok": True}

    _install_fake_retrieval_module(monkeypatch, fake_cleanup)

    asyncio.run(scheduler.automated_chat_cleanup())

    assert calls["cleanup"] == 1


def test_automated_cleanup_skips_when_lock_unavailable(monkeypatch):
    """
    Strict single-run policy: if distributed lock cannot be guaranteed, skip cleanup.
    """
    _patch_common_scheduler_dependencies(
        monkeypatch,
        lock_acquired=False,
        lock_error=RuntimeError("redis unavailable"),
    )

    calls = {"cleanup": 0}

    async def fake_cleanup(*args, **kwargs):
        calls["cleanup"] += 1
        return {"ok": True}

    _install_fake_retrieval_module(monkeypatch, fake_cleanup)

    asyncio.run(scheduler.automated_chat_cleanup())

    assert calls["cleanup"] == 0


def test_automated_cleanup_uses_executor_for_heavy_work(monkeypatch):
    """
    Expected non-blocking behavior: cleanup orchestration should offload heavy work to an executor
    to avoid starving the event loop.
    """
    _patch_common_scheduler_dependencies(monkeypatch, lock_acquired=True)

    async def fake_cleanup(*args, **kwargs):
        return {"ok": True}

    _install_fake_retrieval_module(monkeypatch, fake_cleanup)

    class FakeLoop:
        def __init__(self):
            self.run_in_executor_called = 0

        async def run_in_executor(self, executor, fn):
            self.run_in_executor_called += 1
            # Run callable in a worker thread to mirror real executor behavior.
            return await asyncio.to_thread(fn)

    fake_loop = FakeLoop()
    monkeypatch.setattr(scheduler.asyncio, "get_event_loop", lambda: fake_loop)

    asyncio.run(scheduler.automated_chat_cleanup())

    assert fake_loop.run_in_executor_called > 0


def test_automated_cleanup_runs_with_local_override(monkeypatch):
    """
    When USE_REDIS_LOCKS is False but CHAT_CLEANUP_ALLOW_LOCAL_NO_REDIS is True,
    cleanup should run without distributed locking.
    """
    _patch_common_scheduler_dependencies(monkeypatch, lock_acquired=True)
    monkeypatch.setattr(scheduler, "USE_REDIS_LOCKS", False)
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_ALLOW_LOCAL_NO_REDIS", True)

    calls = {"cleanup": 0}

    async def fake_cleanup(*args, **kwargs):
        calls["cleanup"] += 1
        return {"ok": True}

    _install_fake_retrieval_module(monkeypatch, fake_cleanup)

    asyncio.run(scheduler.automated_chat_cleanup())

    assert calls["cleanup"] == 1


def test_automated_cleanup_skips_without_redis_and_no_local_override(monkeypatch):
    """
    When USE_REDIS_LOCKS is False and CHAT_CLEANUP_ALLOW_LOCAL_NO_REDIS is False,
    cleanup should be skipped for safety.
    """
    _patch_common_scheduler_dependencies(monkeypatch, lock_acquired=True)
    monkeypatch.setattr(scheduler, "USE_REDIS_LOCKS", False)
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_ALLOW_LOCAL_NO_REDIS", False)

    calls = {"cleanup": 0}

    async def fake_cleanup(*args, **kwargs):
        calls["cleanup"] += 1
        return {"ok": True}

    _install_fake_retrieval_module(monkeypatch, fake_cleanup)

    asyncio.run(scheduler.automated_chat_cleanup())

    assert calls["cleanup"] == 0


def test_update_cleanup_schedule_uses_configured_cron_timezone_and_job_options(
    monkeypatch,
):
    """
    Schedule setup should use configured cron/timezone values and bounded APScheduler options.
    """
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_ENABLED", ConfigValue(True))
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_DAYS", ConfigValue(30))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_CRON", "15 3 * * *")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_TIMEZONE", "America/Toronto")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULER_MISFIRE_GRACE_SECONDS", 123)

    class FakeScheduler:
        def __init__(self):
            self.add_job_kwargs = None
            self.removed_job_id = None

        def get_job(self, job_id):
            return "existing-job"

        def remove_job(self, job_id):
            self.removed_job_id = job_id

        def add_job(self, *args, **kwargs):
            self.add_job_kwargs = kwargs

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: fake_scheduler)

    cron_calls = {}

    def fake_from_crontab(expr, timezone):
        cron_calls["expr"] = expr
        cron_calls["timezone"] = timezone
        return "fake-trigger"

    monkeypatch.setattr(scheduler.CronTrigger, "from_crontab", fake_from_crontab)

    scheduler.update_cleanup_schedule()

    assert fake_scheduler.removed_job_id == scheduler.CLEANUP_JOB_ID
    assert cron_calls["expr"] == "15 3 * * *"
    assert cron_calls["timezone"] == "America/Toronto"
    assert fake_scheduler.add_job_kwargs["trigger"] == "fake-trigger"
    assert fake_scheduler.add_job_kwargs["max_instances"] == 1
    assert fake_scheduler.add_job_kwargs["coalesce"] is True
    assert fake_scheduler.add_job_kwargs["misfire_grace_time"] == 123


def test_get_schedule_info_includes_cron_timezone_for_scheduled_job(monkeypatch):
    """
    Schedule info should expose cron/timezone metadata along with the next run.
    """
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_ENABLED", ConfigValue(True))
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_DAYS", ConfigValue(30))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_CRON", "0 4 * * *")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_TIMEZONE", "America/Toronto")
    monkeypatch.setattr(scheduler, "USE_REDIS_LOCKS", True)

    class FakeJob:
        def __init__(self):
            self.next_run_time = datetime(
                2026, 2, 19, 4, 0, tzinfo=timezone(timedelta(hours=-5))
            )
            self.trigger = "cron[hour='4', minute='0']"

    class FakeScheduler:
        def get_job(self, job_id):
            return FakeJob()

    monkeypatch.setattr(scheduler, "get_scheduler", lambda: FakeScheduler())

    info = scheduler.get_schedule_info()

    assert info["status"] == "Scheduled"
    assert info["schedule_cron"] == "0 4 * * *"
    assert info["schedule_timezone"] == "America/Toronto"
    assert info["next_run"] == "2026-02-19T04:00:00-05:00"


def test_get_schedule_info_shows_scheduled_when_redis_unavailable(monkeypatch):
    """
    When USE_REDIS_LOCKS is False, schedule info should still report Scheduled
    since cleanup runs without distributed lock.
    """
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_ENABLED", ConfigValue(True))
    monkeypatch.setattr(scheduler, "CHAT_LIFETIME_DAYS", ConfigValue(30))
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_CRON", "0 2 * * *")
    monkeypatch.setattr(scheduler, "CHAT_CLEANUP_SCHEDULE_TIMEZONE", "Etc/UTC")
    monkeypatch.setattr(scheduler, "USE_REDIS_LOCKS", False)

    class FakeJob:
        def __init__(self):
            self.next_run_time = datetime(2026, 2, 19, 2, 0, tzinfo=timezone.utc)

    class FakeScheduler:
        def get_job(self, job_id):
            return FakeJob()

    monkeypatch.setattr(scheduler, "get_scheduler", lambda: FakeScheduler())

    info = scheduler.get_schedule_info()

    assert info["status"] == "Scheduled"
    assert info["schedule_cron"] == "0 2 * * *"
    assert info["schedule_timezone"] == "Etc/UTC"


def test_describe_cron_schedule_returns_raw_cron():
    """
    Cron helper should return the raw cron expression for UI display.
    """
    expr = "12 2 2 * *"
    assert scheduler.describe_cron_schedule(expr) == expr
