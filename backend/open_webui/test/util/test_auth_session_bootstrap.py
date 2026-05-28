import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from open_webui.routers import auths
from open_webui.utils import auth


def _build_request(cookies=None, headers=None):
    encoded_headers = []

    for key, value in (headers or {}).items():
        encoded_headers.append((key.lower().encode(), str(value).encode()))

    cookie_header = "; ".join(
        f"{key}={value}" for key, value in (cookies or {}).items()
    )
    if cookie_header:
        encoded_headers.append((b"cookie", cookie_header.encode()))

    app = SimpleNamespace(
        state=SimpleNamespace(
            config=SimpleNamespace(
                ACCESS_TOKEN_EXPIRES_IN="5m",
                REFRESH_TOKEN_EXPIRES_IN="7d",
                USER_PERMISSIONS={},
            )
        )
    )

    scope = {
        "type": "http",
        "path": "/api/v1/auths/",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 1234),
        "app": app,
        "state": {"enable_api_key": False},
    }

    return Request(scope)


def _build_user(user_id="user-1"):
    return SimpleNamespace(
        id=user_id,
        email=f"{user_id}@example.com",
        name="Example User",
        role="user",
        profile_image_url="/user.png",
        domain="example.com",
    )


def _build_refresh_session(user_id="user-1"):
    return SimpleNamespace(
        id="session-1",
        user_id=user_id,
        token_hash="stored-hash",
        expires_at=9_999_999_999,
    )


def test_get_session_user_accepts_valid_refresh_cookie_without_bearer(monkeypatch):
    request = _build_request(
        cookies={auth.WEBUI_REFRESH_TOKEN_COOKIE_NAME: "session-1.refresh-secret"}
    )
    response = Response()
    recorded = {}

    monkeypatch.setattr(auths, "_get_active_refresh_session", lambda refresh_token: _build_refresh_session())
    monkeypatch.setattr(auths, "verify_refresh_token", lambda *args, **kwargs: True)
    monkeypatch.setattr(auths.Users, "get_user_by_id", lambda user_id: _build_user(user_id))
    monkeypatch.setattr(auths, "get_permissions", lambda *args, **kwargs: {"ok": True})

    def issue_tokens(*args, **kwargs):
        recorded.update(kwargs)
        return "fresh-access", 12345

    monkeypatch.setattr(auths, "_issue_tokens_for_user", issue_tokens)

    result = asyncio.run(auths.get_session_user(request, response, None))

    assert result["token"] == "fresh-access"
    assert result["expires_at"] == 12345
    assert recorded["current_refresh_session_id"] == "session-1"
    assert recorded["current_refresh_token_hash"] == "stored-hash"


def test_get_session_user_rejects_invalid_refresh_cookie_even_with_bearer(monkeypatch):
    request = _build_request(
        cookies={auth.WEBUI_REFRESH_TOKEN_COOKIE_NAME: "session-1.refresh-secret"},
    )
    response = Response()
    revoked = []

    monkeypatch.setattr(auths, "_get_active_refresh_session", lambda refresh_token: _build_refresh_session())
    monkeypatch.setattr(auths, "verify_refresh_token", lambda *args, **kwargs: False)
    monkeypatch.setattr(auths.RefreshSessions, "revoke_session_by_id", revoked.append)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auths.get_session_user(
                request,
                response,
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials="valid-access-token"
                ),
            )
        )

    assert exc_info.value.status_code == 400
    assert revoked == []


def test_get_session_user_rejects_bearer_only_bootstrap(monkeypatch):
    response = Response()
    bearer_auth = auth.ResolvedAuthContext(
        user=_build_user(),
        token="valid-access-token",
        source="bearer",
    )

    monkeypatch.setattr(auths, "get_permissions", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        auths,
        "resolve_current_user",
        lambda request, auth_token, require_auth=False: bearer_auth,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auths.get_session_user(_build_request(), response, None))

    assert exc_info.value.status_code == 403

def test_get_session_user_allows_legacy_cookie_migration(monkeypatch):
    response = Response()
    legacy_cookie_auth = auth.ResolvedAuthContext(
        user=_build_user(),
        token="legacy-cookie-token",
        source="legacy_cookie",
    )

    monkeypatch.setattr(auths, "get_permissions", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        auths,
        "resolve_current_user",
        lambda request, auth_token, require_auth=False: legacy_cookie_auth,
    )
    monkeypatch.setattr(
        auths,
        "_issue_tokens_for_user",
        lambda *args, **kwargs: ("fresh-access", 12345),
    )

    migrated = asyncio.run(auths.get_session_user(_build_request(), response, None))

    assert migrated["token"] == "fresh-access"


def test_get_current_user_optional_ignores_invalid_token():
    request = _build_request()
    invalid_auth = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="invalid-access-token"
    )

    assert auth.get_current_user_optional(request, invalid_auth) is None