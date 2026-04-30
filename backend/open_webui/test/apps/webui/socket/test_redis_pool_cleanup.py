"""
Tests for the Redis user/session pool cleanup feature.

Covers:
- flush_user_and_session_pools() logic in socket/main.py
- automated_redis_pool_cleanup() scheduled task in scheduler.py
- schedule_redis_pool_cleanup() schedule management
- disconnect() USER_POOL/SESSION_POOL consistency logic
- Edge cases: empty pools, partial stale data, temp tokens, Redis errors
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock

# Prevent uvicorn import errors in test environment
sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLock:
    """Configurable fake distributed lock for testing."""

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
        self.lock_obtained = False
        return True


class DictPool(dict):
    """
    Plain dict that mimics the RedisDict interface used in socket/main.py.
    Supports keys(), get(), __contains__, __setitem__, __delitem__, etc.
    """

    pass


class FakeSioManager:
    def __init__(self, active_sids=None):
        self.active_sids = set(active_sids or [])

    def is_connected(self, sid, namespace="/"):
        return sid in self.active_sids


class FakeSio:
    def __init__(self, active_sids=None):
        self.manager = FakeSioManager(active_sids=active_sids)


# =====================================================================
# Tests for flush_user_and_session_pools (socket/main.py)
# =====================================================================


class TestFlushUserAndSessionPools:
    """Unit tests for the flush_user_and_session_pools function."""

    def _patch_pools(self, monkeypatch, user_pool, session_pool, active_sids=None):
        """Patch USER_POOL and SESSION_POOL in socket.main module."""
        from open_webui.socket import main as socket_main

        monkeypatch.setattr(socket_main, "USER_POOL", user_pool)
        monkeypatch.setattr(socket_main, "SESSION_POOL", session_pool)
        monkeypatch.setattr(socket_main, "sio", FakeSio(active_sids=active_sids))

    def test_removes_stale_user_entries_with_no_active_sessions(self, monkeypatch):
        """
        If a USER_POOL entry references SIDs that don't exist in SESSION_POOL,
        the entire user entry should be removed.
        """
        user_pool = DictPool(
            {
                "user-1": ["sid-A", "sid-B"],  # sid-A and sid-B not in SESSION_POOL
                "user-2": ["sid-C"],  # sid-C exists
            }
        )
        session_pool = DictPool(
            {
                "sid-C": {"id": "user-2", "name": "User 2"},
            }
        )
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids={"sid-C"})

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "user-1" not in user_pool
        assert "user-2" in user_pool
        assert user_pool["user-2"] == ["sid-C"]
        assert stats["stale_user_entries_removed"] == 1
        assert stats["stale_sids_removed"] == 2

    def test_removes_stale_sids_but_keeps_user_with_active_sessions(self, monkeypatch):
        """
        If a user has some stale SIDs and some active SIDs, only the stale
        ones should be removed — the user entry should survive.
        """
        user_pool = DictPool(
            {
                "user-1": ["sid-A", "sid-B", "sid-C"],
            }
        )
        session_pool = DictPool(
            {
                "sid-A": {"id": "user-1", "name": "User 1"},
                # sid-B, sid-C are stale
            }
        )
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids={"sid-A"})

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "user-1" in user_pool
        assert user_pool["user-1"] == ["sid-A"]
        assert stats["stale_sids_removed"] == 2
        assert stats["stale_user_entries_removed"] == 0

    def test_removes_orphaned_session_pool_entries(self, monkeypatch):
        """
        SESSION_POOL entries whose SID does not appear in any USER_POOL list
        should be removed as orphaned.
        """
        user_pool = DictPool(
            {
                "user-1": ["sid-A"],
            }
        )
        session_pool = DictPool(
            {
                "sid-A": {"id": "user-1", "name": "User 1"},
                "sid-ORPHAN": {"id": "user-X", "name": "Ghost"},
            }
        )
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids={"sid-A"})

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools(prune_session_pool=True)

        assert "sid-ORPHAN" not in session_pool
        assert "sid-A" in session_pool
        assert stats["orphaned_sessions_removed"] == 1

    def test_removes_stale_temp_tokens(self, monkeypatch):
        """
        Temporary token entries (prefixed with _temp_token_) left behind during
        websocket connect should be cleaned up.
        """
        user_pool = DictPool({})
        session_pool = DictPool(
            {
                "_temp_token_sid-old-1": "some-token-value",
                "_temp_token_sid-old-2": "another-token-value",
            }
        )
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids=set())

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "_temp_token_sid-old-1" not in session_pool
        assert "_temp_token_sid-old-2" not in session_pool
        assert stats["temp_tokens_removed"] == 2

    def test_no_op_when_all_entries_are_valid(self, monkeypatch):
        """When all USER_POOL SIDs exist in SESSION_POOL, nothing is removed."""
        user_pool = DictPool(
            {
                "user-1": ["sid-A"],
                "user-2": ["sid-B", "sid-C"],
            }
        )
        session_pool = DictPool(
            {
                "sid-A": {"id": "user-1", "name": "User 1"},
                "sid-B": {"id": "user-2", "name": "User 2"},
                "sid-C": {"id": "user-2", "name": "User 2"},
            }
        )
        self._patch_pools(
            monkeypatch,
            user_pool,
            session_pool,
            active_sids={"sid-A", "sid-B", "sid-C"},
        )

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert stats["stale_user_entries_removed"] == 0
        assert stats["stale_sids_removed"] == 0
        assert stats["orphaned_sessions_removed"] == 0
        assert stats["users_before"] == stats["users_after"] == 2
        assert stats["sessions_before"] == stats["sessions_after"] == 3

    def test_returns_correct_before_after_counts(self, monkeypatch):
        """Stats should accurately reflect the pool sizes before and after cleanup."""
        user_pool = DictPool(
            {
                "user-1": ["sid-A"],  # stale
                "user-2": ["sid-B"],  # active
                "user-3": ["sid-C"],  # stale
            }
        )
        session_pool = DictPool(
            {
                "sid-B": {"id": "user-2", "name": "User 2"},
            }
        )
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids={"sid-B"})

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert stats["users_before"] == 3
        assert stats["users_after"] == 1
        assert stats["stale_user_entries_removed"] == 2

    def test_handles_mixed_stale_and_temp_tokens(self, monkeypatch):
        """
        A realistic scenario: some active users, some stale users, orphaned
        sessions, and leftover temp tokens — all cleaned in one pass.
        """
        user_pool = DictPool(
            {
                "user-active": ["sid-1", "sid-2"],
                "user-stale": ["sid-gone-1", "sid-gone-2"],
                "user-partial": ["sid-3", "sid-gone-3"],
            }
        )
        session_pool = DictPool(
            {
                "sid-1": {"id": "user-active", "name": "Active"},
                "sid-2": {"id": "user-active", "name": "Active"},
                "sid-3": {"id": "user-partial", "name": "Partial"},
                "sid-orphan": {"id": "user-X", "name": "Orphan"},
                "_temp_token_sid-old": "stale-token",
            }
        )
        self._patch_pools(
            monkeypatch,
            user_pool,
            session_pool,
            active_sids={"sid-1", "sid-2", "sid-3"},
        )

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools(prune_session_pool=True)

        # user-active: all SIDs valid → kept
        assert user_pool["user-active"] == ["sid-1", "sid-2"]
        # user-stale: no valid SIDs → removed
        assert "user-stale" not in user_pool
        # user-partial: sid-3 valid, sid-gone-3 stale → kept with sid-3 only
        assert user_pool["user-partial"] == ["sid-3"]
        # orphan session removed
        assert "sid-orphan" not in session_pool
        # temp token removed
        assert "_temp_token_sid-old" not in session_pool
        # active sessions survive
        assert "sid-1" in session_pool
        assert "sid-2" in session_pool
        assert "sid-3" in session_pool

        assert stats["stale_user_entries_removed"] == 1
        assert stats["stale_sids_removed"] == 3  # sid-gone-1, sid-gone-2, sid-gone-3
        assert stats["orphaned_sessions_removed"] == 1
        assert stats["temp_tokens_removed"] == 1
        assert stats["active_sids_detected"] == 3  # sid-1, sid-2, sid-3

    def test_removes_mirrored_stale_sid_when_not_connected(self, monkeypatch):
        """
        If a SID exists in both USER_POOL and SESSION_POOL but is not actively
        connected, it should be removed from USER_POOL.
        """
        user_pool = DictPool({"user-1": ["sid-stale"]})
        session_pool = DictPool({"sid-stale": {"id": "user-1", "name": "User 1"}})
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids=set())

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "user-1" not in user_pool
        assert stats["stale_user_entries_removed"] == 1
        assert stats["stale_sids_removed"] == 1

    def test_does_not_prune_session_pool_by_default(self, monkeypatch):
        """
        Default safety mode should keep SESSION_POOL entries (except temp tokens)
        even when disconnected.
        """
        user_pool = DictPool({"user-1": ["sid-live"]})
        session_pool = DictPool(
            {
                "sid-live": {"id": "user-1", "name": "User 1"},
                "sid-disconnected": {"id": "user-2", "name": "User 2"},
            }
        )
        self._patch_pools(
            monkeypatch, user_pool, session_pool, active_sids={"sid-live"}
        )

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "sid-disconnected" in session_pool
        assert stats["orphaned_sessions_removed"] == 0

    def test_prunes_disconnected_session_pool_entries_when_enabled(self, monkeypatch):
        """
        Aggressive mode should remove disconnected SESSION_POOL entries.
        """
        user_pool = DictPool({"user-1": ["sid-live"]})
        session_pool = DictPool(
            {
                "sid-live": {"id": "user-1", "name": "User 1"},
                "sid-disconnected": {"id": "user-2", "name": "User 2"},
            }
        )
        self._patch_pools(
            monkeypatch, user_pool, session_pool, active_sids={"sid-live"}
        )

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools(prune_session_pool=True)

        assert "sid-disconnected" not in session_pool
        assert stats["orphaned_sessions_removed"] == 1

    def test_handles_non_list_sids_in_user_pool(self, monkeypatch):
        """
        If USER_POOL contains a non-list value for a user (data corruption),
        the entry should be treated as having no active sessions and removed.
        """
        user_pool = DictPool({"user-corrupt": "not-a-list", "user-ok": ["sid-A"]})
        session_pool = DictPool({"sid-A": {"id": "user-ok", "name": "User OK"}})
        self._patch_pools(monkeypatch, user_pool, session_pool, active_sids={"sid-A"})

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert "user-corrupt" not in user_pool
        assert "user-ok" in user_pool
        assert stats["stale_user_entries_removed"] == 1

    def test_handles_is_connected_exception_gracefully(self, monkeypatch):
        """
        If sio.manager.is_connected raises an exception for a SID, that SID
        should be treated as disconnected (fail-safe to stale).
        """
        from open_webui.socket import main as socket_main

        class ExplodingManager:
            def is_connected(self, sid, namespace="/"):
                if sid == "sid-boom":
                    raise RuntimeError("Internal state corrupted")
                return sid == "sid-ok"

        class ExplodingSio:
            manager = ExplodingManager()

        user_pool = DictPool({"user-1": ["sid-ok", "sid-boom"]})
        session_pool = DictPool(
            {
                "sid-ok": {"id": "user-1", "name": "User 1"},
                "sid-boom": {"id": "user-1", "name": "User 1"},
            }
        )
        monkeypatch.setattr(socket_main, "USER_POOL", user_pool)
        monkeypatch.setattr(socket_main, "SESSION_POOL", session_pool)
        monkeypatch.setattr(socket_main, "sio", ExplodingSio())

        from open_webui.socket.main import flush_user_and_session_pools

        stats = flush_user_and_session_pools()

        assert user_pool["user-1"] == ["sid-ok"]
        assert stats["stale_sids_removed"] == 1


# =====================================================================
# Tests for automated_redis_pool_cleanup (scheduler.py)
# =====================================================================


class TestAutomatedRedisPoolCleanup:
    """Tests for the scheduled cleanup task in scheduler.py."""

    def _patch_scheduler(self, monkeypatch, lock_acquired=True, lock_error=None):
        """Patch scheduler module globals for test isolation."""
        from open_webui import scheduler as sched_mod

        FakeLock.acquire_result = lock_acquired
        FakeLock.acquire_error = lock_error

        monkeypatch.setattr(sched_mod, "USE_REDIS_LOCKS", True)
        monkeypatch.setattr(sched_mod, "REDIS_URL", "redis://test")
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_ENABLED", True)
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_SCHEDULE_CRON", "0 0 * * *")
        monkeypatch.setattr(
            sched_mod, "REDIS_POOL_CLEANUP_SCHEDULE_TIMEZONE", "America/Toronto"
        )
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_LOCK_TIMEOUT", 60)
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_PRUNE_SESSION_POOL", False)
        monkeypatch.setattr(sched_mod, "RedisLock", FakeLock)

    def test_cleanup_runs_when_lock_acquired(self, monkeypatch):
        """Cleanup should execute flush_user_and_session_pools when lock is acquired."""
        from open_webui import scheduler as sched_mod

        self._patch_scheduler(monkeypatch, lock_acquired=True)

        calls = {"flush": 0}

        def fake_flush(**kwargs):
            calls["flush"] += 1
            calls["kwargs"] = kwargs
            return {"ok": True}

        # Install fake socket.main module with required exports
        socket_module = types.ModuleType("open_webui.socket.main")
        socket_module.flush_user_and_session_pools = fake_flush
        monkeypatch.setitem(sys.modules, "open_webui.socket.main", socket_module)

        asyncio.run(sched_mod.automated_redis_pool_cleanup())

        assert calls["flush"] == 1
        assert calls["kwargs"] == {"prune_session_pool": False}

    def test_cleanup_skips_when_lock_unavailable(self, monkeypatch):
        """Cleanup should skip when another replica holds the lock."""
        from open_webui import scheduler as sched_mod

        self._patch_scheduler(monkeypatch, lock_acquired=False)

        calls = {"flush": 0}

        def fake_flush(**kwargs):
            calls["flush"] += 1
            return {}

        socket_module = types.ModuleType("open_webui.socket.main")
        socket_module.flush_user_and_session_pools = fake_flush
        monkeypatch.setitem(sys.modules, "open_webui.socket.main", socket_module)

        asyncio.run(sched_mod.automated_redis_pool_cleanup())

        assert calls["flush"] == 0

    def test_cleanup_skips_when_redis_locks_unavailable(self, monkeypatch):
        """Cleanup should be a no-op when Redis locks are unavailable."""
        from open_webui import scheduler as sched_mod

        self._patch_scheduler(monkeypatch)
        monkeypatch.setattr(sched_mod, "USE_REDIS_LOCKS", False)

        calls = {"flush": 0}

        def fake_flush(**kwargs):
            calls["flush"] += 1
            return {}

        socket_module = types.ModuleType("open_webui.socket.main")
        socket_module.flush_user_and_session_pools = fake_flush
        monkeypatch.setitem(sys.modules, "open_webui.socket.main", socket_module)

        asyncio.run(sched_mod.automated_redis_pool_cleanup())

        assert calls["flush"] == 0


# =====================================================================
# Tests for schedule management
# =====================================================================


class TestRedisPoolCleanupSchedule:
    """Tests for schedule_redis_pool_cleanup."""

    def _patch_scheduler_config(self, monkeypatch):
        """Patch scheduler module config for schedule tests."""
        from open_webui import scheduler as sched_mod

        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_ENABLED", True)
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_SCHEDULE_CRON", "0 0 * * *")
        monkeypatch.setattr(
            sched_mod, "REDIS_POOL_CLEANUP_SCHEDULE_TIMEZONE", "America/Toronto"
        )
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_LOCK_TIMEOUT", 60)
        monkeypatch.setattr(sched_mod, "REDIS_POOL_CLEANUP_PRUNE_SESSION_POOL", False)
        monkeypatch.setattr(sched_mod, "USE_REDIS_LOCKS", True)
        monkeypatch.setattr(sched_mod, "REDIS_URL", "redis://test")
        return sched_mod

    def test_schedule_creates_job_with_correct_cron(self, monkeypatch):
        """Schedule setup should use the configured cron expression and timezone."""
        sched_mod = self._patch_scheduler_config(monkeypatch)

        class FakeScheduler:
            def __init__(self):
                self.add_job_kwargs = None
                self.removed_job_id = None

            def get_job(self, job_id):
                return None  # No existing job

            def add_job(self, *args, **kwargs):
                self.add_job_kwargs = kwargs

        fake_scheduler = FakeScheduler()
        monkeypatch.setattr(sched_mod, "get_scheduler", lambda: fake_scheduler)

        cron_calls = {}

        def fake_from_crontab(expr, timezone):
            cron_calls["expr"] = expr
            cron_calls["timezone"] = timezone
            return "fake-trigger"

        monkeypatch.setattr(sched_mod.CronTrigger, "from_crontab", fake_from_crontab)

        sched_mod.schedule_redis_pool_cleanup()

        assert cron_calls["expr"] == "0 0 * * *"
        assert cron_calls["timezone"] == "America/Toronto"
        assert fake_scheduler.add_job_kwargs["trigger"] == "fake-trigger"
        assert fake_scheduler.add_job_kwargs["max_instances"] == 1
        assert fake_scheduler.add_job_kwargs["coalesce"] is True
        assert (
            fake_scheduler.add_job_kwargs["id"] == sched_mod.REDIS_POOL_CLEANUP_JOB_ID
        )

    def test_schedule_skips_when_redis_locks_unavailable(self, monkeypatch):
        """No job should be created when Redis locks are unavailable."""
        sched_mod = self._patch_scheduler_config(monkeypatch)
        monkeypatch.setattr(sched_mod, "USE_REDIS_LOCKS", False)

        class FakeScheduler:
            def __init__(self):
                self.add_job_called = False

            def get_job(self, job_id):
                return None

            def add_job(self, *args, **kwargs):
                self.add_job_called = True

        fake_scheduler = FakeScheduler()
        monkeypatch.setattr(sched_mod, "get_scheduler", lambda: fake_scheduler)

        sched_mod.schedule_redis_pool_cleanup()

        assert fake_scheduler.add_job_called is False
