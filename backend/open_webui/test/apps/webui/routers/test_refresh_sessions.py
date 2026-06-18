import time
from datetime import timedelta

import pytest

from open_webui.test.util.abstract_integration_test import AbstractPostgresTest


class TestRefreshSessions(AbstractPostgresTest):
    def setup_class(cls):
        super().setup_class()
        from open_webui.models.refresh_sessions_table import RefreshSessions

        cls.refresh_sessions = RefreshSessions

    def test_access_token_helpers_keep_legacy_compatibility(self):
        from open_webui.utils.auth import (
            create_access_token,
            create_token,
            decode_access_token,
            decode_token,
        )

        access_token = create_access_token(
            {"id": "user-1"}, expires_delta=timedelta(minutes=5)
        )
        assert decode_access_token(access_token)["id"] == "user-1"

        legacy_token = create_token(
            {"id": "user-1"}, expires_delta=timedelta(minutes=5)
        )
        assert decode_token(legacy_token)["id"] == "user-1"

    def test_refresh_token_helpers(self):
        from open_webui.utils.auth import (
            create_refresh_token,
            get_refresh_token_session_id,
            parse_refresh_token,
            verify_refresh_token,
        )

        refresh_session_id, refresh_token, refresh_token_hash = create_refresh_token()

        assert get_refresh_token_session_id(refresh_token) == refresh_session_id
        assert verify_refresh_token(refresh_token, refresh_token_hash) is True

        _, mismatched_refresh_token, _ = create_refresh_token(
            session_id=refresh_session_id
        )
        assert (
            verify_refresh_token(mismatched_refresh_token, refresh_token_hash) is False
        )

        with pytest.raises(ValueError):
            parse_refresh_token("invalid-refresh-token")

    def test_create_and_get_refresh_session(self):
        expires_at = int(time.time()) + 3600

        refresh_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-1",
            expires_at=expires_at,
            meta={"ip": "127.0.0.1"},
        )

        assert refresh_session is not None
        assert refresh_session.user_id == "user-1"
        assert refresh_session.token_hash == "hash-1"

        fetched_session = self.refresh_sessions.get_session_by_id(refresh_session.id)

        assert fetched_session is not None
        assert fetched_session.id == refresh_session.id
        assert fetched_session.meta == {"ip": "127.0.0.1"}

    def test_get_active_and_revoke_refresh_session(self):
        refresh_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-1",
            expires_at=int(time.time()) + 3600,
        )

        assert (
            self.refresh_sessions.get_active_session_by_id(refresh_session.id)
            is not None
        )

        assert self.refresh_sessions.revoke_session_by_id(refresh_session.id) is True
        assert (
            self.refresh_sessions.get_active_session_by_id(refresh_session.id) is None
        )

    def test_rotate_refresh_session_token(self):
        refresh_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-1",
            expires_at=int(time.time()) + 3600,
        )

        rotated_session = self.refresh_sessions.rotate_session_token(
            refresh_session.id,
            "hash-2",
            int(time.time()) + 7200,
        )

        assert rotated_session is not None
        assert rotated_session.token_hash == "hash-2"
        assert rotated_session.expires_at > refresh_session.expires_at

    def test_delete_expired_sessions(self):
        expired_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-expired",
            expires_at=int(time.time()) - 10,
        )
        active_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-active",
            expires_at=int(time.time()) + 3600,
        )

        deleted_count = self.refresh_sessions.delete_expired_sessions()

        assert deleted_count == 1
        assert self.refresh_sessions.get_session_by_id(expired_session.id) is None
        assert self.refresh_sessions.get_session_by_id(active_session.id) is not None

    def test_rotate_refresh_session_token_fails_on_stale_hash(self):
        refresh_session = self.refresh_sessions.create_session(
            user_id="user-1",
            token_hash="hash-1",
            expires_at=int(time.time()) + 3600,
        )

        result = self.refresh_sessions.rotate_session_token(
            refresh_session.id,
            "hash-2",
            int(time.time()) + 7200,
            current_token_hash="wrong-hash",
        )

        assert result is None

        unchanged = self.refresh_sessions.get_session_by_id(refresh_session.id)
        assert unchanged is not None
        assert unchanged.token_hash == "hash-1"
