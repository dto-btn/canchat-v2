from fastapi import Response
import pytest

from open_webui.utils import auth


def test_issue_tokens_for_user_rotates_refresh_token_for_existing_session(
    monkeypatch,
):
    response = Response()
    recorded = {"rotate": None, "cookie": None}

    def create_refresh_token(session_id=None):
        return session_id or "session-1", "session-1.new-secret", "new-hash"

    def rotate_session_token(
        session_id, token_hash, expires_at, current_token_hash=None
    ):
        recorded["rotate"] = (
            session_id,
            token_hash,
            expires_at,
            current_token_hash,
        )
        return object()

    def set_refresh_token_cookie(_response, refresh_token, expires_at_refresh_token):
        recorded["cookie"] = (refresh_token, expires_at_refresh_token)

    monkeypatch.setattr(auth, "create_refresh_token", create_refresh_token)
    monkeypatch.setattr(
        auth.RefreshSessions, "rotate_session_token", rotate_session_token
    )
    monkeypatch.setattr(auth, "set_refresh_token_cookie", set_refresh_token_cookie)

    access_token, expires_at = auth.issue_tokens_for_user(
        user_id="user-1",
        response=response,
        access_token_expires_in="5m",
        refresh_token_expires_in="7d",
        current_refresh_session_id="session-1",
        current_refresh_token_hash="stored-hash",
    )

    assert access_token
    assert expires_at is not None
    assert recorded["rotate"] is not None
    assert recorded["rotate"][0] == "session-1"
    assert recorded["rotate"][1] == "new-hash"
    assert recorded["rotate"][3] == "stored-hash"
    assert recorded["cookie"] is not None
    assert recorded["cookie"][0] == "session-1.new-secret"
    assert recorded["cookie"][1] == recorded["rotate"][2]


def test_issue_tokens_for_user_rejects_on_compare_and_swap_rotation_failure(
    monkeypatch,
):
    response = Response()

    def create_refresh_token(session_id=None):
        return session_id or "session-1", "session-1.new-secret", "new-hash"

    monkeypatch.setattr(auth, "create_refresh_token", create_refresh_token)
    monkeypatch.setattr(
        auth.RefreshSessions,
        "rotate_session_token",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(Exception) as exc_info:
        auth.issue_tokens_for_user(
            user_id="user-1",
            response=response,
            access_token_expires_in="5m",
            refresh_token_expires_in="7d",
            current_refresh_session_id="session-1",
            current_refresh_token_hash="stored-hash",
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert (
        getattr(exc_info.value, "detail", None)
        == auth.ERROR_MESSAGES.REFRESH_SESSION_CONFLICT
    )
