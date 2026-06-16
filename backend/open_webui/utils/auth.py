"""Helpers for access-token issuance, refresh cookies, and user resolution."""

import logging
import secrets
import uuid
import jwt

from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple, Optional, Union

from open_webui.models.users import UserModel, Users
from open_webui.models.refresh_sessions_table import RefreshSessions

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import (
    WEBUI_REFRESH_TOKEN_COOKIE_NAME,
    WEBUI_REFRESH_TOKEN_COOKIE_SAME_SITE,
    WEBUI_REFRESH_TOKEN_COOKIE_SECURE,
    WEBUI_SECRET_KEY,
    SRC_LOG_LEVELS,
)
from open_webui.utils.misc import parse_duration

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

logging.getLogger("passlib").setLevel(logging.ERROR)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


SESSION_SECRET = WEBUI_SECRET_KEY
ALGORITHM = "HS256"
REFRESH_TOKEN_SEPARATOR = "."
LEGACY_AUTH_TOKEN_COOKIE_NAME = "token"

##############
# Auth Utils
##############

bearer_security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


AuthSource = Literal["bearer", "legacy_cookie", "api_key"]


class ResolvedAuthContext(NamedTuple):
    # Callers use the source to enforce rollout-specific behavior, such as only
    # allowing legacy-cookie bootstrap on the migration endpoint.
    user: UserModel
    token: str
    source: AuthSource


def log_auth_event(
    event: str,
    *,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **details,
):
    logger = getattr(log, level, log.info)
    detail_parts = [
        f"{key}={value!r}"
        for key, value in sorted(details.items())
        if value is not None
    ]
    message = f"[auth] {event}"
    if detail_parts:
        message += " | " + " ".join(detail_parts)
    logger(message)


def seconds_until(
    expires_at: Optional[int], current_time: Optional[int] = None
) -> Optional[int]:
    if expires_at is None:
        return None

    reference_time = (
        current_time if current_time is not None else int(datetime.now(UTC).timestamp())
    )
    return expires_at - reference_time


def verify_password(plain_password, hashed_password):
    return (
        pwd_context.verify(plain_password, hashed_password) if hashed_password else None
    )


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(
    data: dict, expires_delta: Union[timedelta, None] = None
) -> str:
    payload = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
        payload.update({"exp": expire})

    encoded_jwt = jwt.encode(payload, SESSION_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    try:
        decoded = jwt.decode(token, SESSION_SECRET, algorithms=[ALGORITHM])
        return decoded
    except Exception:
        return None


def parse_refresh_token(refresh_token: str) -> tuple[str, str]:
    try:
        refresh_session_id, refresh_token_secret = refresh_token.split(
            REFRESH_TOKEN_SEPARATOR, 1
        )
    except ValueError as exc:
        raise ValueError(ERROR_MESSAGES.INVALID_TOKEN) from exc

    if not refresh_session_id or not refresh_token_secret:
        raise ValueError(ERROR_MESSAGES.INVALID_TOKEN)

    return refresh_session_id, refresh_token_secret


def get_refresh_token_session_id(refresh_token: str) -> str:
    refresh_session_id, _ = parse_refresh_token(refresh_token)
    return refresh_session_id


def hash_refresh_token(refresh_token: str) -> str:
    _, refresh_token_secret = parse_refresh_token(refresh_token)
    return pwd_context.hash(refresh_token_secret)


def verify_refresh_token(refresh_token: str, refresh_token_hash: Optional[str]) -> bool:
    if not refresh_token_hash:
        return False

    try:
        _, refresh_token_secret = parse_refresh_token(refresh_token)
    except ValueError:
        return False

    try:
        return pwd_context.verify(refresh_token_secret, refresh_token_hash)
    except Exception:
        return False


def create_refresh_token(session_id: Optional[str] = None) -> tuple[str, str, str]:
    refresh_session_id = session_id or str(uuid.uuid4())
    refresh_token_secret = secrets.token_urlsafe(32)
    refresh_token = (
        f"{refresh_session_id}{REFRESH_TOKEN_SEPARATOR}{refresh_token_secret}"
    )
    refresh_token_hash = hash_refresh_token(refresh_token)
    return refresh_session_id, refresh_token, refresh_token_hash


def create_token(data: dict, expires_delta: Union[timedelta, None] = None) -> str:
    return create_access_token(data, expires_delta)


def decode_token(token: str) -> Optional[dict]:
    return decode_access_token(token)


def get_access_token_expiration(
    access_token_expires_in: str,
) -> tuple[Optional[timedelta], Optional[int]]:
    expires_delta_access_token = parse_duration(access_token_expires_in)
    expires_at_access_token = None
    if expires_delta_access_token:
        expires_at_access_token = int(datetime.now(UTC).timestamp()) + int(
            expires_delta_access_token.total_seconds()
        )

    return expires_delta_access_token, expires_at_access_token


def get_refresh_token_expiration(
    refresh_token_expires_in: str,
) -> tuple[timedelta, int]:
    expires_delta_refresh_token = parse_duration(refresh_token_expires_in)
    if expires_delta_refresh_token is None:
        raise HTTPException(
            status_code=500,
            detail="Refresh token expiry must be finite.",
        )

    expires_at_refresh_token = int(datetime.now(UTC).timestamp()) + int(
        expires_delta_refresh_token.total_seconds()
    )
    return expires_delta_refresh_token, expires_at_refresh_token


def clear_legacy_auth_cookie(response: Response):
    response.delete_cookie(LEGACY_AUTH_TOKEN_COOKIE_NAME)


def set_refresh_token_cookie(
    response: Response, refresh_token: str, expires_at_refresh_token: int
):
    datetime_expires_at_refresh = datetime.fromtimestamp(expires_at_refresh_token, UTC)
    refresh_session_id = get_refresh_token_session_id(refresh_token)

    response.set_cookie(
        key=WEBUI_REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        expires=datetime_expires_at_refresh,
        httponly=True,
        samesite=WEBUI_REFRESH_TOKEN_COOKIE_SAME_SITE,
        secure=WEBUI_REFRESH_TOKEN_COOKIE_SECURE,
    )
    log_auth_event(
        "refresh-cookie-set",
        refresh_session_id=refresh_session_id,
        refresh_expires_at=expires_at_refresh_token,
        refresh_remaining_seconds=seconds_until(expires_at_refresh_token),
    )


def issue_tokens_for_user(
    user_id: str,
    response: Response,
    access_token_expires_in: str,
    refresh_token_expires_in: str,
    current_refresh_session_id: Optional[str] = None,
    current_refresh_expires_at: Optional[int] = None,
    current_refresh_token_hash: Optional[str] = None,
    meta: Optional[dict] = None,
) -> tuple[str, Optional[int]]:
    current_time = int(datetime.now(UTC).timestamp())
    expires_delta_access_token, expires_at_access_token = get_access_token_expiration(
        access_token_expires_in
    )

    access_token = create_token(
        data={"id": user_id},
        expires_delta=expires_delta_access_token,
    )

    log_auth_event(
        "access-token-issued",
        user_id=user_id,
        access_token_expires_at=expires_at_access_token,
        access_token_remaining_seconds=seconds_until(
            expires_at_access_token, current_time
        ),
    )

    clear_legacy_auth_cookie(response)

    _, expires_at_refresh_token = get_refresh_token_expiration(refresh_token_expires_in)
    refresh_session_id, refresh_token, refresh_token_hash = create_refresh_token(
        current_refresh_session_id
    )

    if current_refresh_session_id is not None:
        refresh_session = RefreshSessions.rotate_session_token(
            session_id=current_refresh_session_id,
            token_hash=refresh_token_hash,
            expires_at=expires_at_refresh_token,
            current_token_hash=current_refresh_token_hash,
        )
    else:
        refresh_session = RefreshSessions.create_session(
            user_id=user_id,
            token_hash=refresh_token_hash,
            expires_at=expires_at_refresh_token,
            session_id=refresh_session_id,
            meta=meta,
        )

    # Failing to refresh
    if refresh_session is None:
        if current_refresh_session_id is not None:
            log_auth_event(
                "refresh-session-rotation-failed",
                level="warning",
                user_id=user_id,
                refresh_session_id=current_refresh_session_id,
            )
            raise HTTPException(409, detail=ERROR_MESSAGES.REFRESH_SESSION_CONFLICT)

        log_auth_event(
            "refresh-session-create-failed",
            level="error",
            user_id=user_id,
            refresh_session_id=refresh_session_id,
        )
        raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT())

    log_auth_event(
        (
            "refresh-session-rotated"
            if current_refresh_session_id is not None
            else "refresh-session-created"
        ),
        user_id=user_id,
        refresh_session_id=refresh_session_id,
        refresh_expires_at=expires_at_refresh_token,
        refresh_remaining_seconds=seconds_until(expires_at_refresh_token, current_time),
    )

    set_refresh_token_cookie(response, refresh_token, expires_at_refresh_token)
    return access_token, expires_at_access_token


def get_legacy_auth_token(request: Request) -> Optional[str]:
    return request.cookies.get(LEGACY_AUTH_TOKEN_COOKIE_NAME)


def create_api_key():
    key = str(uuid.uuid4()).replace("-", "")
    return f"sk-{key}"


def resolve_current_user(
    request: Request,
    auth_token: Optional[HTTPAuthorizationCredentials] = None,
    require_auth: bool = True,
) -> Optional[ResolvedAuthContext]:
    """Resolve bearer, legacy-cookie, or API-key auth and return the winning source."""
    token = None
    auth_source: Optional[AuthSource] = None

    if auth_token is not None:
        token = auth_token.credentials
        auth_source = "bearer"

    if token is None:
        # Temporary rollout fallback for legacy browser sessions that still rely
        # on the old access-token cookie.
        token = get_legacy_auth_token(request)
        if token is not None:
            auth_source = "legacy_cookie"

    if token is None:
        if require_auth:
            raise HTTPException(status_code=403, detail="Not authenticated")
        return None

    # auth by api key
    if token.startswith("sk-"):
        if not request.state.enable_api_key:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
            )

        if request.app.state.config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS:
            allowed_paths = [
                path.strip()
                for path in str(
                    request.app.state.config.API_KEY_ALLOWED_ENDPOINTS
                ).split(",")
            ]

            if request.url.path not in allowed_paths:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
                )

        user = get_current_user_by_api_key(token)
        return ResolvedAuthContext(user=user, token=token, source="api_key")

    # auth by jwt token
    try:
        data = decode_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if data is not None and "id" in data:
        user = Users.get_user_by_id(data["id"])
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.INVALID_TOKEN,
            )

        Users.update_user_last_active_by_id(user.id)
        return ResolvedAuthContext(
            user=user,
            token=token,
            source=auth_source or "bearer",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ERROR_MESSAGES.UNAUTHORIZED,
    )


def get_current_user(
    request: Request,
    auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
) -> UserModel:
    resolved_auth = resolve_current_user(request, auth_token, require_auth=True)
    return resolved_auth.user


def get_current_user_optional(
    request: Request,
    auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
) -> Optional[UserModel]:
    try:
        resolved_auth = resolve_current_user(request, auth_token, require_auth=False)
    except HTTPException:
        return None

    return resolved_auth.user if resolved_auth else None


def get_current_user_by_api_key(api_key: str):
    user = Users.get_user_by_api_key(api_key)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.INVALID_TOKEN,
        )
    else:
        Users.update_user_last_active_by_id(user.id)

    return user


def get_verified_user(user=Depends(get_current_user)):
    if user.role not in {"user", "admin", "analyst", "global_analyst"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


def get_admin_user(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


def get_metrics_user(user=Depends(get_current_user)):
    if user.role not in {"admin", "analyst", "global_analyst"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user
