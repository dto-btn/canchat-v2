import asyncio
import sys
import types
from types import SimpleNamespace

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from open_webui.routers import retrieval


def _make_chat(chat_id: str, file_ids: list[str]):
    """Build a minimal chat object matching cleanup extractor expectations."""
    return SimpleNamespace(
        id=chat_id,
        chat={
            "messages": [
                {
                    "files": [
                        {"id": file_id, "file": {"id": file_id}} for file_id in file_ids
                    ],
                }
            ]
        },
    )


def test_streaming_cleanup_stops_when_deletion_makes_no_progress(monkeypatch):
    """
    Cleanup must stop early when a fetched batch yields zero deletions to avoid infinite loops.
    """
    monkeypatch.setattr(retrieval, "get_knowledge_base_file_ids", lambda: set())
    monkeypatch.setattr(
        retrieval,
        "get_all_file_references_from_chats",
        lambda exclude_chat_ids=None: {},
    )

    calls = {"batch": 0, "file_cleanup": 0}

    def fake_get_chat_batch_for_cleanup(*args, **kwargs):
        calls["batch"] += 1
        if calls["batch"] == 1:
            return [_make_chat("chat-1", ["file-1"])]
        return []

    monkeypatch.setattr(
        retrieval, "get_chat_batch_for_cleanup", fake_get_chat_batch_for_cleanup
    )

    async def fake_delete_chats_with_retry(chat_ids, max_retries=3, context_label=None):
        return {"deleted_count": 0, "errors": []}

    monkeypatch.setattr(
        retrieval, "delete_chats_with_retry", fake_delete_chats_with_retry
    )

    async def fake_cleanup_orphaned_files(*args, **kwargs):
        calls["file_cleanup"] += 1
        return {"files_cleaned": 0, "collections_cleaned": 0, "errors": []}

    monkeypatch.setattr(
        retrieval, "cleanup_orphaned_files", fake_cleanup_orphaned_files
    )
    monkeypatch.setattr(
        retrieval, "emit_chat_deletion_notification", lambda *a, **k: None
    )

    result = asyncio.run(
        retrieval.cleanup_expired_chats_streaming(
            max_age_days=30,
            preserve_pinned=True,
            preserve_archived=False,
            force_cleanup_all=False,
        )
    )

    assert calls["batch"] == 1
    assert calls["file_cleanup"] == 0
    assert result["chats_deleted"] == 0
    assert any("no chats were deleted" in msg.lower() for msg in result["errors"])


def test_streaming_cleanup_skips_file_cleanup_on_partial_chat_deletion(monkeypatch):
    """
    On partial chat deletion, direct batch file cleanup is skipped and deferred reconciliation is used.
    """
    monkeypatch.setattr(retrieval, "get_knowledge_base_file_ids", lambda: set())
    ref_calls = {"count": 0}

    def fake_get_all_refs(exclude_chat_ids=None):
        ref_calls["count"] += 1
        # Initial scan sees both files in-use. Reconciliation scan sees only file-2 in use.
        if ref_calls["count"] == 1:
            return {"file-1": 1, "file-2": 1}
        return {"file-2": 1}

    monkeypatch.setattr(
        retrieval,
        "get_all_file_references_from_chats",
        fake_get_all_refs,
    )

    calls = {"batch": 0, "file_cleanup": 0}

    def fake_get_chat_batch_for_cleanup(*args, **kwargs):
        calls["batch"] += 1
        if calls["batch"] == 1:
            return [
                _make_chat("chat-1", ["file-1"]),
                _make_chat("chat-2", ["file-2"]),
            ]
        return []

    monkeypatch.setattr(
        retrieval, "get_chat_batch_for_cleanup", fake_get_chat_batch_for_cleanup
    )

    async def fake_delete_chats_with_retry(chat_ids, max_retries=3, context_label=None):
        return {"deleted_count": 1, "errors": []}

    monkeypatch.setattr(
        retrieval, "delete_chats_with_retry", fake_delete_chats_with_retry
    )

    async def fake_cleanup_orphaned_files(*args, **kwargs):
        calls["file_cleanup"] += 1
        assert kwargs["file_ids"] == {"file-1", "file-2"}
        assert kwargs["exclude_from_chats"] == {"file-2"}
        return {"files_cleaned": 0, "collections_cleaned": 0, "errors": []}

    monkeypatch.setattr(
        retrieval, "cleanup_orphaned_files", fake_cleanup_orphaned_files
    )

    notifications = {"count": 0}

    async def fake_emit(*args, **kwargs):
        notifications["count"] += 1

    monkeypatch.setattr(retrieval, "emit_chat_deletion_notification", fake_emit)

    result = asyncio.run(
        retrieval.cleanup_expired_chats_streaming(
            max_age_days=30,
            preserve_pinned=True,
            preserve_archived=False,
            force_cleanup_all=False,
        )
    )

    # Single batch is processed; deferred reconciliation runs after the loop.
    assert calls["batch"] == 1
    assert ref_calls["count"] == 2
    # Deferred reconciliation should run once after loop.
    assert calls["file_cleanup"] == 1
    assert notifications["count"] == 1
    assert any("partial chat deletion" in msg.lower() for msg in result["errors"])


def test_streaming_cleanup_stops_when_lock_lost_before_deletion(monkeypatch):
    """
    Cleanup should stop before deleting chats when should_continue reports lock loss.
    """
    monkeypatch.setattr(retrieval, "get_knowledge_base_file_ids", lambda: set())
    monkeypatch.setattr(
        retrieval,
        "get_all_file_references_from_chats",
        lambda exclude_chat_ids=None: {},
    )
    monkeypatch.setattr(
        retrieval,
        "get_chat_batch_for_cleanup",
        lambda *args, **kwargs: [_make_chat("chat-1", ["file-1"])],
    )

    calls = {"delete": 0}

    async def fake_delete_chats_with_retry(*args, **kwargs):
        calls["delete"] += 1
        return {"deleted_count": 1, "errors": []}

    monkeypatch.setattr(
        retrieval, "delete_chats_with_retry", fake_delete_chats_with_retry
    )
    monkeypatch.setattr(
        retrieval, "emit_chat_deletion_notification", lambda *a, **k: None
    )

    should_continue_calls = {"count": 0}

    def should_continue():
        should_continue_calls["count"] += 1
        # First call (loop start) allows execution, second call (before deletion) stops.
        return should_continue_calls["count"] == 1

    result = asyncio.run(
        retrieval.cleanup_expired_chats_streaming(
            max_age_days=30,
            preserve_pinned=True,
            preserve_archived=False,
            force_cleanup_all=False,
            should_continue=should_continue,
        )
    )

    assert calls["delete"] == 0
    assert any("before chat deletion" in msg.lower() for msg in result["errors"])


def test_admin_cleanup_endpoint_requires_distributed_lock(monkeypatch):
    """
    Admin-triggered cleanup should fail fast when Redis lock safety cannot be guaranteed.
    """
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_ENABLED", SimpleNamespace(value=True)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_DAYS", SimpleNamespace(value=30)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_PINNED",
        SimpleNamespace(value=True),
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_ARCHIVED",
        SimpleNamespace(value=False),
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_LOCK_TIMEOUT",
        1800,
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_LOCK_RENEWAL_INTERVAL",
        300,
    )

    class DummyLock:
        def __init__(self, redis_url, lock_name, timeout_secs):
            self.lock_obtained = False
            self.last_error = RuntimeError("redis unavailable")

        async def acquire_lock(self):
            return False

        async def release_lock(self):
            return False

    monkeypatch.setattr("open_webui.env.USE_REDIS_LOCKS", True)
    monkeypatch.setattr("open_webui.env.REDIS_URL", "redis://test")
    monkeypatch.setattr("open_webui.socket.utils.RedisLock", DummyLock)

    async def fake_renew(*args, **kwargs):
        return None

    monkeypatch.setattr("open_webui.socket.utils.renew_lock_periodically", fake_renew)

    try:
        asyncio.run(retrieval.api_cleanup_expired_chats(user=None))
        assert False, "Expected HTTPException"
    except Exception as exc:
        from fastapi import HTTPException

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 503


def test_admin_cleanup_endpoint_returns_conflict_when_lock_already_held(monkeypatch):
    """
    Admin-triggered cleanup should return 409 when another cleanup owner already holds the lock.
    """
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_ENABLED", SimpleNamespace(value=True)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_DAYS", SimpleNamespace(value=30)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_PINNED",
        SimpleNamespace(value=True),
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_ARCHIVED",
        SimpleNamespace(value=False),
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_LOCK_TIMEOUT",
        1800,
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_LOCK_RENEWAL_INTERVAL",
        300,
    )

    class DummyLock:
        def __init__(self, redis_url, lock_name, timeout_secs):
            self.lock_obtained = False
            self.last_error = None

        async def acquire_lock(self):
            return False

        async def release_lock(self):
            return False

    monkeypatch.setattr("open_webui.env.USE_REDIS_LOCKS", True)
    monkeypatch.setattr("open_webui.env.REDIS_URL", "redis://test")
    monkeypatch.setattr("open_webui.socket.utils.RedisLock", DummyLock)

    async def fake_renew(*args, **kwargs):
        return None

    monkeypatch.setattr("open_webui.socket.utils.renew_lock_periodically", fake_renew)

    try:
        asyncio.run(retrieval.api_cleanup_expired_chats(user=None))
        assert False, "Expected HTTPException"
    except Exception as exc:
        from fastapi import HTTPException

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 409


def test_admin_cleanup_endpoint_runs_without_redis_locks(monkeypatch):
    """
    When USE_REDIS_LOCKS is False, admin cleanup should return 503 because
    the API endpoint requires distributed locking for safety.
    """
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_ENABLED", SimpleNamespace(value=True)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_LIFETIME_DAYS", SimpleNamespace(value=30)
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_PINNED",
        SimpleNamespace(value=True),
    )
    monkeypatch.setattr(
        "open_webui.config.CHAT_CLEANUP_PRESERVE_ARCHIVED",
        SimpleNamespace(value=False),
    )
    monkeypatch.setattr("open_webui.env.USE_REDIS_LOCKS", False)

    try:
        asyncio.run(retrieval.api_cleanup_expired_chats(user=None))
        assert False, "Expected HTTPException"
    except Exception as exc:
        from fastapi import HTTPException

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 503
