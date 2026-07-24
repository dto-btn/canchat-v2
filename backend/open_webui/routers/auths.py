import re
import uuid
import logging
import time
from aiohttp import ClientSession

from open_webui.models.auths import (
    AddUserForm,
    ApiKey,
    Token,
    LdapForm,
    SigninForm,
    SigninResponse,
    SignupForm,
    UpdatePasswordForm,
    UpdateProfileForm,
    UserResponse,
)
from open_webui.models.auths_table import Auths
from open_webui.models.refresh_sessions import RefreshSessionModel
from open_webui.models.users import Users
from open_webui.models.users import UserModel
from open_webui.models.refresh_sessions_table import RefreshSessions

from open_webui.constants import ERROR_MESSAGES, WEBHOOK_MESSAGES
from open_webui.env import (
    WEBUI_AUTH,
    WEBUI_REFRESH_TOKEN_COOKIE_NAME,
    WEBUI_AUTH_TRUSTED_EMAIL_HEADER,
    WEBUI_AUTH_TRUSTED_NAME_HEADER,
    SRC_LOG_LEVELS,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse, Response
from open_webui.config import (
    OPENID_PROVIDER_URL,
)
from pydantic import BaseModel
from open_webui.utils.misc import validate_email_format
from open_webui.utils.auth import (
    AuthLogEvent,
    build_refresh_session_meta,
    clear_legacy_auth_cookie,
    create_api_key,
    bearer_security,
    get_admin_user,
    get_verified_user,
    get_current_user,
    get_password_hash,
    get_refresh_token_session_id,
    issue_tokens_for_user,
    log_auth_event,
    resolve_current_user,
    verify_refresh_token,
)
from open_webui.utils.webhook import post_webhook
from open_webui.utils.access_control import get_permissions

from typing import Any, NoReturn, Optional

from ssl import CERT_REQUIRED, PROTOCOL_TLS
from ldap3 import Server, Connection, NONE, Tls
from ldap3.utils.conv import escape_filter_chars

router = APIRouter()

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])
TOKEN_DURATION_PATTERN = re.compile(r"^(-1|0|(-?\d+(\.\d+)?)(ms|s|m|h|d|w))$")

############################
# GetSessionUser
############################


class SessionUserResponse(Token, UserResponse):
    expires_at: Optional[int] = None
    permissions: Optional[dict] = None


class AdminConfig(BaseModel):
    SHOW_ADMIN_DETAILS: bool
    WEBUI_URL: str
    ENABLE_SIGNUP: bool
    ENABLE_API_KEY: bool
    ENABLE_API_KEY_ENDPOINT_RESTRICTIONS: bool
    API_KEY_ALLOWED_ENDPOINTS: str
    ENABLE_CHANNELS: bool
    DEFAULT_USER_ROLE: str
    ACCESS_TOKEN_EXPIRES_IN: str
    REFRESH_TOKEN_EXPIRES_IN: str
    JWT_EXPIRES_IN: Optional[str] = None
    ENABLE_COMMUNITY_SHARING: bool
    ENABLE_MESSAGE_RATING: bool


def _issue_tokens_for_user(
    request: Request,
    response: Response,
    user_id: str,
    current_refresh_session_id: Optional[str] = None,
    current_refresh_token_hash: Optional[str] = None,
) -> tuple[str, Optional[int]]:
    return issue_tokens_for_user(
        user_id=user_id,
        response=response,
        access_token_expires_in=request.app.state.config.ACCESS_TOKEN_EXPIRES_IN,
        refresh_token_expires_in=request.app.state.config.REFRESH_TOKEN_EXPIRES_IN,
        current_refresh_session_id=current_refresh_session_id,
        current_refresh_token_hash=current_refresh_token_hash,
        meta=build_refresh_session_meta(request),
    )


def _get_active_refresh_session(refresh_token: str):
    try:
        refresh_session_id = get_refresh_token_session_id(refresh_token)
    except ValueError as exc:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_REFRESH_TOKEN) from exc

    refresh_session = RefreshSessions.get_active_session_by_id(refresh_session_id)
    if refresh_session is None:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_REFRESH_TOKEN)

    return refresh_session


def _raise_invalid_refresh_token(
    refresh_session_id: Optional[str] = None,
) -> NoReturn:
    if refresh_session_id is not None:
        RefreshSessions.revoke_session_by_id(refresh_session_id)

    raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_REFRESH_TOKEN)


def _get_refresh_session_user(
    refresh_token: str,
) -> tuple[RefreshSessionModel, UserModel]:
    refresh_session = _get_active_refresh_session(refresh_token)
    if not verify_refresh_token(refresh_token, refresh_session.token_hash):
        _raise_invalid_refresh_token(refresh_session.id)

    user = Users.get_user_by_id(refresh_session.user_id)
    if not user:
        _raise_invalid_refresh_token(refresh_session.id)

    return refresh_session, user


def _build_session_user_response(
    request: Request,
    user: UserModel,
    token: str,
    expires_at: Optional[int],
) -> dict[str, Any]:
    user_permissions = get_permissions(
        user.id, request.app.state.config.USER_PERMISSIONS
    )

    return {
        "token": token,
        "token_type": "Bearer",
        "expires_at": expires_at,
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "profile_image_url": user.profile_image_url,
        "permissions": user_permissions,
        "domain": getattr(user, "domain", None),
    }


def _build_admin_config_response(request: Request) -> AdminConfig:
    app_config = request.app.state.config
    return AdminConfig(
        SHOW_ADMIN_DETAILS=app_config.SHOW_ADMIN_DETAILS,
        WEBUI_URL=app_config.WEBUI_URL,
        ENABLE_SIGNUP=app_config.ENABLE_SIGNUP,
        ENABLE_API_KEY=app_config.ENABLE_API_KEY,
        ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=app_config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
        API_KEY_ALLOWED_ENDPOINTS=app_config.API_KEY_ALLOWED_ENDPOINTS,
        ENABLE_CHANNELS=app_config.ENABLE_CHANNELS,
        DEFAULT_USER_ROLE=app_config.DEFAULT_USER_ROLE,
        ACCESS_TOKEN_EXPIRES_IN=app_config.ACCESS_TOKEN_EXPIRES_IN,
        REFRESH_TOKEN_EXPIRES_IN=app_config.REFRESH_TOKEN_EXPIRES_IN,
        JWT_EXPIRES_IN=app_config.ACCESS_TOKEN_EXPIRES_IN,
        ENABLE_COMMUNITY_SHARING=app_config.ENABLE_COMMUNITY_SHARING,
        ENABLE_MESSAGE_RATING=app_config.ENABLE_MESSAGE_RATING,
    )


def _get_refresh_failure_reason(exc: HTTPException) -> str:
    match exc.detail:
        case ERROR_MESSAGES.MISSING_REFRESH_TOKEN:
            return "missing_refresh_token"
        case ERROR_MESSAGES.INVALID_REFRESH_TOKEN:
            return "invalid_refresh_token"
        case ERROR_MESSAGES.REFRESH_SESSION_CONFLICT:
            return "refresh_session_conflict"

    return f"http_{exc.status_code}"


@router.get("/", response_model=SessionUserResponse)
async def get_session_user(
    request: Request,
    response: Response,
    auth_token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_security),
):
    refresh_token = request.cookies.get(WEBUI_REFRESH_TOKEN_COOKIE_NAME)
    if refresh_token:
        refresh_session, user = _get_refresh_session_user(refresh_token)

        token, expires_at = _issue_tokens_for_user(
            request,
            response,
            user.id,
            current_refresh_session_id=refresh_session.id,
            current_refresh_token_hash=refresh_session.token_hash,
        )
    else:
        auth_context = resolve_current_user(request, auth_token, require_auth=False)
        if auth_context is None:
            raise HTTPException(status_code=403, detail="Not authenticated")

        if auth_context.source != "legacy_cookie":
            raise HTTPException(status_code=403, detail="Not authenticated")

        user = auth_context.user
        token, expires_at = _issue_tokens_for_user(request, response, user.id)

    return _build_session_user_response(request, user, token, expires_at)


############################
# Update Profile
############################


@router.post("/update/profile", response_model=UserResponse)
async def update_profile(
    form_data: UpdateProfileForm, session_user=Depends(get_verified_user)
):
    if session_user:
        user = Users.update_user_by_id(
            session_user.id,
            {"profile_image_url": form_data.profile_image_url, "name": form_data.name},
        )
        if user:
            return user
        else:
            raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT())
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# Update Password
############################


@router.post("/update/password", response_model=bool)
async def update_password(
    form_data: UpdatePasswordForm, session_user=Depends(get_current_user)
):
    if WEBUI_AUTH_TRUSTED_EMAIL_HEADER:
        raise HTTPException(400, detail=ERROR_MESSAGES.ACTION_PROHIBITED)

    user = Auths.authenticate_user(session_user.email, form_data.password)

    if user:
        hashed = get_password_hash(form_data.new_password)
        password_updated = Auths.update_user_password_by_id(user.id, hashed)
        if password_updated:
            RefreshSessions.revoke_sessions_by_user_id(user.id)

        return password_updated

    raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_PASSWORD)


############################
# LDAP Authentication
############################
@router.post("/ldap", response_model=SessionUserResponse)
async def ldap_auth(request: Request, response: Response, form_data: LdapForm):
    ENABLE_LDAP = request.app.state.config.ENABLE_LDAP
    LDAP_SERVER_LABEL = request.app.state.config.LDAP_SERVER_LABEL
    LDAP_SERVER_HOST = request.app.state.config.LDAP_SERVER_HOST
    LDAP_SERVER_PORT = request.app.state.config.LDAP_SERVER_PORT
    LDAP_ATTRIBUTE_FOR_MAIL = request.app.state.config.LDAP_ATTRIBUTE_FOR_MAIL
    LDAP_ATTRIBUTE_FOR_USERNAME = request.app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME
    LDAP_SEARCH_BASE = request.app.state.config.LDAP_SEARCH_BASE
    LDAP_SEARCH_FILTERS = request.app.state.config.LDAP_SEARCH_FILTERS
    LDAP_APP_DN = request.app.state.config.LDAP_APP_DN
    LDAP_APP_PASSWORD = request.app.state.config.LDAP_APP_PASSWORD
    LDAP_USE_TLS = request.app.state.config.LDAP_USE_TLS
    LDAP_CA_CERT_FILE = request.app.state.config.LDAP_CA_CERT_FILE
    LDAP_CIPHERS = (
        request.app.state.config.LDAP_CIPHERS
        if request.app.state.config.LDAP_CIPHERS
        else "ALL"
    )

    if not ENABLE_LDAP:
        raise HTTPException(400, detail="LDAP authentication is not enabled")

    try:
        tls = Tls(
            validate=CERT_REQUIRED,
            version=PROTOCOL_TLS,
            ca_certs_file=LDAP_CA_CERT_FILE,
            ciphers=LDAP_CIPHERS,
        )
    except Exception as e:
        log.error(f"An error occurred on TLS: {str(e)}")
        raise HTTPException(400, detail=str(e))

    try:
        server = Server(
            host=LDAP_SERVER_HOST,
            port=LDAP_SERVER_PORT,
            get_info=NONE,
            use_ssl=LDAP_USE_TLS,
            tls=tls,
        )
        connection_app = Connection(
            server,
            LDAP_APP_DN,
            LDAP_APP_PASSWORD,
            auto_bind="NONE",
            authentication="SIMPLE",
        )
        if not connection_app.bind():
            raise HTTPException(400, detail="Application account bind failed")

        search_success = connection_app.search(
            search_base=LDAP_SEARCH_BASE,
            search_filter=f"(&({LDAP_ATTRIBUTE_FOR_USERNAME}={escape_filter_chars(form_data.user.lower())}){LDAP_SEARCH_FILTERS})",
            attributes=[
                f"{LDAP_ATTRIBUTE_FOR_USERNAME}",
                f"{LDAP_ATTRIBUTE_FOR_MAIL}",
                "cn",
            ],
        )

        if not search_success:
            raise HTTPException(400, detail="User not found in the LDAP server")

        entry = connection_app.entries[0]
        username = str(entry[f"{LDAP_ATTRIBUTE_FOR_USERNAME}"]).lower()
        mail = str(entry[f"{LDAP_ATTRIBUTE_FOR_MAIL}"])
        if not mail or mail == "" or mail == "[]":
            raise HTTPException(400, f"User {form_data.user} does not have mail.")
        cn = str(entry["cn"])
        user_dn = entry.entry_dn

        if username == form_data.user.lower():
            connection_user = Connection(
                server,
                user_dn,
                form_data.password,
                auto_bind="NONE",
                authentication="SIMPLE",
            )
            if not connection_user.bind():
                raise HTTPException(400, f"Authentication failed for {form_data.user}")

            user = Users.get_user_by_email(mail)
            if not user:
                try:
                    role = (
                        "admin"
                        if Users.get_num_users() == 0
                        else request.app.state.config.DEFAULT_USER_ROLE
                    )

                    user = Auths.insert_new_auth(
                        email=mail,
                        password=str(uuid.uuid4()),
                        name=cn,
                        role=role,
                        domain=mail.split("@")[1],
                    )

                    if not user:
                        raise HTTPException(
                            500, detail=ERROR_MESSAGES.CREATE_USER_ERROR
                        )

                except HTTPException:
                    raise
                except Exception as err:
                    raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT(err))

            user = Auths.authenticate_user_by_trusted_header(mail)

            if user:
                token, expires_at = _issue_tokens_for_user(request, response, user.id)
                return _build_session_user_response(request, user, token, expires_at)
            else:
                raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)
        else:
            raise HTTPException(
                400,
                f"User {form_data.user} does not match the record. Search result: {str(entry[f'{LDAP_ATTRIBUTE_FOR_USERNAME}'])}",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, detail=str(e))


############################
# SignIn
############################


@router.post("/signin", response_model=SessionUserResponse)
async def signin(request: Request, response: Response, form_data: SigninForm):
    if WEBUI_AUTH_TRUSTED_EMAIL_HEADER:
        if WEBUI_AUTH_TRUSTED_EMAIL_HEADER not in request.headers:
            raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_TRUSTED_HEADER)

        trusted_email = request.headers[WEBUI_AUTH_TRUSTED_EMAIL_HEADER].lower()
        trusted_name = trusted_email
        if WEBUI_AUTH_TRUSTED_NAME_HEADER:
            trusted_name = request.headers.get(
                WEBUI_AUTH_TRUSTED_NAME_HEADER, trusted_email
            )
        if not Users.get_user_by_email(trusted_email.lower()):
            await signup(
                request,
                response,
                SignupForm(
                    email=trusted_email, password=str(uuid.uuid4()), name=trusted_name
                ),
            )
        user = Auths.authenticate_user_by_trusted_header(trusted_email)
    elif WEBUI_AUTH == False:
        admin_email = "admin@localhost"
        admin_password = "admin"

        if Users.get_user_by_email(admin_email.lower()):
            user = Auths.authenticate_user(admin_email.lower(), admin_password)
        else:
            if Users.get_num_users() != 0:
                raise HTTPException(400, detail=ERROR_MESSAGES.EXISTING_USERS)

            await signup(
                request,
                response,
                SignupForm(email=admin_email, password=admin_password, name="User"),
            )

            user = Auths.authenticate_user(admin_email.lower(), admin_password)
    else:
        user = Auths.authenticate_user(form_data.email.lower(), form_data.password)

    if user:
        access_token, expires_at_access_token = _issue_tokens_for_user(
            request, response, user.id
        )
        return _build_session_user_response(
            request, user, access_token, expires_at_access_token
        )
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# SignUp
############################


@router.post("/signup", response_model=SessionUserResponse)
async def signup(request: Request, response: Response, form_data: SignupForm):
    if WEBUI_AUTH:
        if (
            not request.app.state.config.ENABLE_SIGNUP
            or not request.app.state.config.ENABLE_LOGIN_FORM
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
            )
    else:
        if Users.get_num_users() != 0:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
            )

    if not validate_email_format(form_data.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_EMAIL_FORMAT
        )

    if Users.get_user_by_email(form_data.email.lower()):
        raise HTTPException(400, detail=ERROR_MESSAGES.EMAIL_TAKEN)

    try:
        role = (
            "admin"
            if Users.get_num_users() == 0
            else request.app.state.config.DEFAULT_USER_ROLE
        )

        if Users.get_num_users() == 0:
            # Disable signup after the first user is created
            request.app.state.config.ENABLE_SIGNUP = False

        hashed = get_password_hash(form_data.password)
        user = Auths.insert_new_auth(
            form_data.email.lower(),
            hashed,
            form_data.name,
            form_data.profile_image_url,
            role,
            domain=form_data.email.split("@")[1],
        )

        if user:
            token, expires_at = _issue_tokens_for_user(request, response, user.id)

            if request.app.state.config.WEBHOOK_URL:
                post_webhook(
                    request.app.state.config.WEBHOOK_URL,
                    WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                    {
                        "action": "signup",
                        "message": WEBHOOK_MESSAGES.USER_SIGNUP(user.name),
                        "user": user.model_dump_json(exclude_none=True),
                    },
                )

            return _build_session_user_response(request, user, token, expires_at)
        else:
            raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT(err))


@router.get("/signout")
async def signout(request: Request, response: Response):
    refresh_token = request.cookies.get(WEBUI_REFRESH_TOKEN_COOKIE_NAME)
    if refresh_token:
        try:
            refresh_session = _get_active_refresh_session(refresh_token)
            RefreshSessions.revoke_session_by_id(refresh_session.id)
        except HTTPException:
            pass

    clear_legacy_auth_cookie(response)
    response.delete_cookie(WEBUI_REFRESH_TOKEN_COOKIE_NAME)
    response.delete_cookie("oauth_id_token")
    response.delete_cookie("oauth_access_token")

    oauth_id_token = request.cookies.get("oauth_id_token")
    if oauth_id_token:
        try:
            async with ClientSession() as session:
                async with session.get(OPENID_PROVIDER_URL.value) as resp:
                    if resp.status == 200:
                        openid_data = await resp.json()
                        logout_url = openid_data.get("end_session_endpoint")
                        if logout_url:
                            redirect_response = RedirectResponse(
                                url=f"{logout_url}?id_token_hint={oauth_id_token}"
                            )
                            clear_legacy_auth_cookie(redirect_response)
                            redirect_response.delete_cookie(
                                WEBUI_REFRESH_TOKEN_COOKIE_NAME
                            )
                            redirect_response.delete_cookie("oauth_id_token")
                            redirect_response.delete_cookie("oauth_access_token")
                            return redirect_response
                    else:
                        raise HTTPException(
                            status_code=resp.status,
                            detail="Failed to fetch OpenID configuration",
                        )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": True}


############################
# Refresh Token
############################


@router.post("/refresh", response_model=SessionUserResponse)
def refresh_token(request: Request, response: Response):
    refresh_started_at = time.perf_counter()

    try:
        refresh_token = request.cookies.get(WEBUI_REFRESH_TOKEN_COOKIE_NAME)
        if not refresh_token:
            raise HTTPException(400, detail=ERROR_MESSAGES.MISSING_REFRESH_TOKEN)

        refresh_session, user = _get_refresh_session_user(refresh_token)

        access_token, expires_at_access_token = _issue_tokens_for_user(
            request,
            response,
            user.id,
            current_refresh_session_id=refresh_session.id,
            current_refresh_token_hash=refresh_session.token_hash,
        )

        latency_ms = (time.perf_counter() - refresh_started_at) * 1000
        log_auth_event(
            AuthLogEvent.REFRESH_TOKEN_SUCCEEDED,
            user_id=user.id,
            refresh_session_id=refresh_session.id,
            latency_ms=round(latency_ms, 2),
        )

        return _build_session_user_response(
            request, user, access_token, expires_at_access_token
        )
    except HTTPException as exc:
        latency_ms = (time.perf_counter() - refresh_started_at) * 1000
        reason = _get_refresh_failure_reason(exc)
        log_auth_event(
            AuthLogEvent.REFRESH_TOKEN_FAILED,
            level="warning" if exc.status_code < 500 else "error",
            reason=reason,
            status_code=exc.status_code,
            latency_ms=round(latency_ms, 2),
        )
        raise
    except Exception:
        latency_ms = (time.perf_counter() - refresh_started_at) * 1000
        log_auth_event(
            AuthLogEvent.REFRESH_TOKEN_FAILED,
            level="error",
            reason="unexpected_error",
            latency_ms=round(latency_ms, 2),
        )
        raise


############################
# AddUser
############################


@router.post("/add", response_model=UserResponse)
async def add_user(form_data: AddUserForm, user=Depends(get_admin_user)):
    if not validate_email_format(form_data.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_EMAIL_FORMAT
        )

    if Users.get_user_by_email(form_data.email.lower()):
        raise HTTPException(400, detail=ERROR_MESSAGES.EMAIL_TAKEN)

    try:
        hashed = get_password_hash(form_data.password)
        user = Auths.insert_new_auth(
            form_data.email.lower(),
            hashed,
            form_data.name,
            form_data.profile_image_url,
            form_data.role,
            domain=form_data.email.split("@")[1],
        )

        if user:
            return {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "domain": user.domain,
                "profile_image_url": user.profile_image_url,
            }
        else:
            raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)
    except Exception as err:
        raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT(err))


############################
# GetAdminDetails
############################


@router.get("/admin/details")
async def get_admin_details(request: Request, user=Depends(get_current_user)):
    if not request.app.state.config.SHOW_ADMIN_DETAILS:
        raise HTTPException(404, detail=ERROR_MESSAGES.ACTION_PROHIBITED)

    admin_email = request.app.state.config.ADMIN_EMAIL
    admin_name = None

    if admin_email:
        admin = Users.get_user_by_email(admin_email)
        if admin:
            admin_name = admin.name
    else:
        admin = Users.get_first_admin_user()
        if admin:
            admin_email = admin.email
            admin_name = admin.name

    return {
        "name": admin_name,
        "email": admin_email,
    }


############################
# ToggleSignUp
############################


@router.get("/admin/config", response_model=AdminConfig)
async def get_admin_config(request: Request, user=Depends(get_admin_user)):
    return _build_admin_config_response(request)


@router.post("/admin/config", response_model=AdminConfig)
async def update_admin_config(
    request: Request, form_data: AdminConfig, user=Depends(get_admin_user)
):
    app_config = request.app.state.config

    app_config.SHOW_ADMIN_DETAILS = form_data.SHOW_ADMIN_DETAILS
    app_config.WEBUI_URL = form_data.WEBUI_URL
    app_config.ENABLE_SIGNUP = form_data.ENABLE_SIGNUP

    app_config.ENABLE_API_KEY = form_data.ENABLE_API_KEY
    app_config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = (
        form_data.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS
    )
    app_config.API_KEY_ALLOWED_ENDPOINTS = form_data.API_KEY_ALLOWED_ENDPOINTS

    app_config.ENABLE_CHANNELS = form_data.ENABLE_CHANNELS

    if form_data.DEFAULT_USER_ROLE in ["pending", "user", "admin"]:
        app_config.DEFAULT_USER_ROLE = form_data.DEFAULT_USER_ROLE

    access_token_expires_in = (
        form_data.ACCESS_TOKEN_EXPIRES_IN or form_data.JWT_EXPIRES_IN
    )
    if access_token_expires_in and TOKEN_DURATION_PATTERN.match(
        access_token_expires_in
    ):
        app_config.ACCESS_TOKEN_EXPIRES_IN = access_token_expires_in
        app_config.JWT_EXPIRES_IN = access_token_expires_in

    if form_data.REFRESH_TOKEN_EXPIRES_IN and TOKEN_DURATION_PATTERN.match(
        form_data.REFRESH_TOKEN_EXPIRES_IN
    ):
        app_config.REFRESH_TOKEN_EXPIRES_IN = form_data.REFRESH_TOKEN_EXPIRES_IN

    app_config.ENABLE_COMMUNITY_SHARING = form_data.ENABLE_COMMUNITY_SHARING
    app_config.ENABLE_MESSAGE_RATING = form_data.ENABLE_MESSAGE_RATING

    return _build_admin_config_response(request)


class LdapServerConfig(BaseModel):
    label: str
    host: str
    port: Optional[int] = None
    attribute_for_mail: str = "mail"
    attribute_for_username: str = "uid"
    app_dn: str
    app_dn_password: str
    search_base: str
    search_filters: str = ""
    use_tls: bool = True
    certificate_path: Optional[str] = None
    ciphers: Optional[str] = "ALL"


@router.get("/admin/config/ldap/server", response_model=LdapServerConfig)
async def get_ldap_server(request: Request, user=Depends(get_admin_user)):
    return {
        "label": request.app.state.config.LDAP_SERVER_LABEL,
        "host": request.app.state.config.LDAP_SERVER_HOST,
        "port": request.app.state.config.LDAP_SERVER_PORT,
        "attribute_for_mail": request.app.state.config.LDAP_ATTRIBUTE_FOR_MAIL,
        "attribute_for_username": request.app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME,
        "app_dn": request.app.state.config.LDAP_APP_DN,
        "app_dn_password": request.app.state.config.LDAP_APP_PASSWORD,
        "search_base": request.app.state.config.LDAP_SEARCH_BASE,
        "search_filters": request.app.state.config.LDAP_SEARCH_FILTERS,
        "use_tls": request.app.state.config.LDAP_USE_TLS,
        "certificate_path": request.app.state.config.LDAP_CA_CERT_FILE,
        "ciphers": request.app.state.config.LDAP_CIPHERS,
    }


@router.post("/admin/config/ldap/server")
async def update_ldap_server(
    request: Request, form_data: LdapServerConfig, user=Depends(get_admin_user)
):
    required_fields = [
        "label",
        "host",
        "attribute_for_mail",
        "attribute_for_username",
        "app_dn",
        "app_dn_password",
        "search_base",
    ]
    for key in required_fields:
        value = getattr(form_data, key)
        if not value:
            raise HTTPException(400, detail=f"Required field {key} is empty")

    if form_data.use_tls and not form_data.certificate_path:
        raise HTTPException(
            400, detail="TLS is enabled but certificate file path is missing"
        )

    request.app.state.config.LDAP_SERVER_LABEL = form_data.label
    request.app.state.config.LDAP_SERVER_HOST = form_data.host
    request.app.state.config.LDAP_SERVER_PORT = form_data.port
    request.app.state.config.LDAP_ATTRIBUTE_FOR_MAIL = form_data.attribute_for_mail
    request.app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME = (
        form_data.attribute_for_username
    )
    request.app.state.config.LDAP_APP_DN = form_data.app_dn
    request.app.state.config.LDAP_APP_PASSWORD = form_data.app_dn_password
    request.app.state.config.LDAP_SEARCH_BASE = form_data.search_base
    request.app.state.config.LDAP_SEARCH_FILTERS = form_data.search_filters
    request.app.state.config.LDAP_USE_TLS = form_data.use_tls
    request.app.state.config.LDAP_CA_CERT_FILE = form_data.certificate_path
    request.app.state.config.LDAP_CIPHERS = form_data.ciphers

    return {
        "label": request.app.state.config.LDAP_SERVER_LABEL,
        "host": request.app.state.config.LDAP_SERVER_HOST,
        "port": request.app.state.config.LDAP_SERVER_PORT,
        "attribute_for_mail": request.app.state.config.LDAP_ATTRIBUTE_FOR_MAIL,
        "attribute_for_username": request.app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME,
        "app_dn": request.app.state.config.LDAP_APP_DN,
        "app_dn_password": request.app.state.config.LDAP_APP_PASSWORD,
        "search_base": request.app.state.config.LDAP_SEARCH_BASE,
        "search_filters": request.app.state.config.LDAP_SEARCH_FILTERS,
        "use_tls": request.app.state.config.LDAP_USE_TLS,
        "certificate_path": request.app.state.config.LDAP_CA_CERT_FILE,
        "ciphers": request.app.state.config.LDAP_CIPHERS,
    }


@router.get("/admin/config/ldap")
async def get_ldap_config(request: Request, user=Depends(get_admin_user)):
    return {"ENABLE_LDAP": request.app.state.config.ENABLE_LDAP}


class LdapConfigForm(BaseModel):
    enable_ldap: Optional[bool] = None


@router.post("/admin/config/ldap")
async def update_ldap_config(
    request: Request, form_data: LdapConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_LDAP = form_data.enable_ldap
    return {"ENABLE_LDAP": request.app.state.config.ENABLE_LDAP}


############################
# API Key
############################


# create api key
@router.post("/api_key", response_model=ApiKey)
async def generate_api_key(request: Request, user=Depends(get_current_user)):
    if not request.app.state.config.ENABLE_API_KEY:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.API_KEY_CREATION_NOT_ALLOWED,
        )

    api_key = create_api_key()
    success = Users.update_user_api_key_by_id(user.id, api_key)

    if success:
        return {
            "api_key": api_key,
        }
    else:
        raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_API_KEY_ERROR)


# delete api key
@router.delete("/api_key", response_model=bool)
async def delete_api_key(user=Depends(get_current_user)):
    success = Users.update_user_api_key_by_id(user.id, None)
    return success


# get api key
@router.get("/api_key", response_model=ApiKey)
async def get_api_key(user=Depends(get_current_user)):
    api_key = Users.get_user_api_key_by_id(user.id)
    if api_key:
        return {
            "api_key": api_key,
        }
    else:
        raise HTTPException(404, detail=ERROR_MESSAGES.API_KEY_NOT_FOUND)
