"""Pure ASGI request context and completion access logging middleware."""

import logging
import re
import time
import uuid
from typing import Any

from open_webui.observability.context import request_id_var, user_id_var
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("open_webui.access")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
HEALTH_PATH_PREFIX = "/health"


class RequestContextMiddleware:
    """Bind request context and log once, after the final response body chunk."""

    def __init__(self, app: ASGIApp):
        self.app = app

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "app":
            object.__setattr__(self, name, value)
        else:
            setattr(self.app, name, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        request_id = self._request_id(scope["headers"])
        state = scope.setdefault("state", {})
        request_token = request_id_var.set(request_id)
        user_token = user_id_var.set(None)
        status_code = 500
        body_size = 0
        final_body_at: float | None = None

        async def send_with_context(message: Message) -> None:
            nonlocal status_code, body_size, final_body_at

            if message["type"] == "http.response.start":
                status_code = message["status"]
                user_id = scope.get("state", {}).get("auth_user_id")
                user_id_var.set(user_id if isinstance(user_id, str) else None)
                headers = [
                    (name, value)
                    for name, value in message.get("headers", ())
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                await send(message)
                body_size += len(body)
                if not message.get("more_body", False):
                    final_body_at = time.perf_counter()
                return

            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            try:
                user_id = state.get("auth_user_id")
                user_id_var.set(user_id if isinstance(user_id, str) else None)
                completed_at = (
                    final_body_at if final_body_at is not None else time.perf_counter()
                )
                duration_ms = (completed_at - started_at) * 1000
                path = scope.get("path", "")
                if not path.startswith(HEALTH_PATH_PREFIX):
                    extra = {
                        "event": "http.request.completed",
                        "http.request.method": scope.get("method", "UNKNOWN"),
                        "url.path": path,
                        "http.response.status_code": status_code,
                        "http.response.body.size": body_size,
                        "duration_ms": round(duration_ms, 3),
                        "client.address": self._client_address(scope),
                    }
                    route = self._route(scope)
                    if route is not None:
                        extra["http.route"] = route
                    log.info("HTTP request completed", extra=extra)
            finally:
                user_id_var.reset(user_token)
                request_id_var.reset(request_token)

    @staticmethod
    def _request_id(headers: list[tuple[bytes, bytes]]) -> str:
        request_ids = [
            value for name, value in headers if name.lower() == b"x-request-id"
        ]
        if len(request_ids) == 1:
            try:
                candidate = request_ids[0].decode("ascii")
            except UnicodeDecodeError:
                candidate = ""
            if REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
        return str(uuid.uuid4())

    @staticmethod
    def _route(scope: Scope) -> str | None:
        route_path = getattr(scope.get("route"), "path", None)
        return route_path if isinstance(route_path, str) else None

    @staticmethod
    def _client_address(scope: Scope) -> str:
        client = scope.get("client")
        return str(client[0]) if client else "unknown"
