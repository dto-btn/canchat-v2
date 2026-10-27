import asyncio
import logging
import re
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
import httpx
from starlette.requests import Request
from starlette.types import Message

from open_webui.observability.context import request_id_var, user_id_var
from open_webui.observability.logging_config import LogContextFilter
from open_webui.observability.middleware import RequestContextMiddleware
from open_webui.utils import auth


def _scope(path="/items/123", headers=(), scope_type="http"):
    return {
        "type": scope_type,
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "client": ("10.0.0.1", 5678),
        "server": ("testserver", 80),
        "state": {},
    }


async def _response_app(_scope, _receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _invoke(middleware, scope):
    messages = []

    async def send(message):
        messages.append(message)

    await middleware(scope, _empty_receive, send)
    return messages


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.fixture
def context_caplog(caplog):
    log_filter = LogContextFilter()
    caplog.handler.addFilter(log_filter)
    try:
        yield caplog
    finally:
        caplog.handler.removeFilter(log_filter)


@pytest.mark.parametrize(
    "incoming, expected",
    [
        (b"request-123", "request-123"),
        (b"bad id", None),
        (b"x" * 65, None),
        (None, None),
    ],
)
def test_request_id_is_validated_and_echoed(incoming, expected, context_caplog):
    caplog = context_caplog
    headers = [] if incoming is None else [(b"x-request-id", incoming)]
    middleware = RequestContextMiddleware(_response_app)

    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        messages = asyncio.run(_invoke(middleware, _scope(headers=headers)))

    response_start = next(m for m in messages if m["type"] == "http.response.start")
    response_id = dict(response_start["headers"])[b"x-request-id"].decode()
    if expected is not None:
        assert response_id == expected
    else:
        assert re.fullmatch(r"[0-9a-f-]{36}", response_id)

    record = next(r for r in caplog.records if r.name == "open_webui.access")
    assert record.request_id == response_id


def test_streaming_access_record_covers_final_chunk_and_counts_bytes(caplog):
    async def streaming_app(scope, _receive, send):
        scope["route"] = SimpleNamespace(path="/items/{item_id}")
        await send({"type": "http.response.start", "status": 206, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await asyncio.sleep(0.04)
        await send({"type": "http.response.body", "body": b"last!", "more_body": False})

    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        asyncio.run(_invoke(RequestContextMiddleware(streaming_app), _scope()))

    records = [r for r in caplog.records if r.name == "open_webui.access"]
    assert len(records) == 1
    record = records[0]
    assert record.duration_ms >= 35
    assert record.__dict__["http.response.body.size"] == 10
    assert record.__dict__["http.response.status_code"] == 206
    assert record.__dict__["http.request.method"] == "GET"
    assert record.__dict__["http.route"] == "/items/{item_id}"
    assert record.__dict__["url.path"] == "/items/123"
    assert record.__dict__["client.address"] == "10.0.0.1"
    assert record.event == "http.request.completed"


@pytest.mark.parametrize("path", ["/health", "/health/ready"])
def test_health_endpoint_has_no_access_record(caplog, path):
    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        asyncio.run(
            _invoke(
                RequestContextMiddleware(_response_app),
                _scope(path=path),
            )
        )
    assert not [r for r in caplog.records if r.name == "open_webui.access"]


def test_non_http_scopes_pass_through_unchanged(caplog):
    received = []

    async def websocket_app(scope, _receive, send):
        received.append(scope["type"])
        await send({"type": "websocket.accept"})

    async def send(message):
        received.append(message["type"])

    async def receive() -> Message:
        return {"type": "websocket.receive", "bytes": b""}

    scope = _scope(path="/ws/socket.io", scope_type="websocket")
    asyncio.run(RequestContextMiddleware(websocket_app)(scope, receive, send))

    assert received == ["websocket", "websocket.accept"]
    assert not [r for r in caplog.records if r.name == "open_webui.access"]
    assert request_id_var.get() is None


def test_socket_io_polling_request_is_logged_as_http(caplog):
    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        messages = asyncio.run(
            _invoke(
                RequestContextMiddleware(_response_app),
                _scope(path="/ws/socket.io/"),
            )
        )

    assert any(m["type"] == "http.response.start" for m in messages)
    records = [r for r in caplog.records if r.name == "open_webui.access"]
    assert len(records) == 1
    assert records[0].__dict__["url.path"] == "/ws/socket.io/"
    assert "http.route" not in records[0].__dict__


def test_concurrent_requests_keep_context_and_identity_isolation(context_caplog):
    caplog = context_caplog
    observed = {}

    async def app(scope, _receive, send):
        request_id = request_id_var.get()
        await asyncio.sleep(0.01)
        scope["state"]["auth_user_id"] = scope["state"]["expected_user_id"]
        observed[scope["state"]["auth_user_id"]] = request_id
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def invoke_one(user_id, request_id):
        scope = _scope(headers=[(b"x-request-id", request_id.encode())])
        scope["state"]["expected_user_id"] = user_id
        await RequestContextMiddleware(app)(scope, _empty_receive, _discard)

    async def run():
        await asyncio.gather(
            invoke_one("user-a", "request-a1"),
            invoke_one("user-b", "request-b1"),
        )

    async def _discard(message):
        return None

    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        asyncio.run(run())

    assert observed == {"user-a": "request-a1", "user-b": "request-b1"}
    assert request_id_var.get() is None
    assert user_id_var.get() is None
    records = [r for r in caplog.records if r.name == "open_webui.access"]
    assert {r.user_id for r in records} == {"user-a", "user-b"}


def test_cancellation_logs_partial_response_and_propagates(caplog):
    async def disconnected_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})
        disconnect = await receive()
        assert disconnect["type"] == "http.disconnect"
        raise asyncio.CancelledError

    async def invoke_disconnected():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            return None

        await RequestContextMiddleware(disconnected_app)(_scope(), receive, send)

    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(invoke_disconnected())

    records = [r for r in caplog.records if r.name == "open_webui.access"]
    assert len(records) == 1
    assert records[0].__dict__["http.response.body.size"] == 4


def test_resolved_identity_is_available_to_sync_route_logs(monkeypatch, context_caplog):
    caplog = context_caplog
    user = SimpleNamespace(id="request-user")
    app = FastAPI()

    def resolve_user(request, auth_token=None, require_auth=True):
        request.state.auth_user_id = user.id
        return auth.ResolvedAuthContext(
            user=user,
            token="jwt-token",
            source="bearer",
        )

    monkeypatch.setattr(auth, "resolve_current_user", resolve_user)

    @app.get("/private")
    def private_route(current_user=Depends(auth.get_current_user)):
        logging.getLogger("open_webui.test.request").info("inside request")
        return {"id": current_user.id}

    async def request():
        transport = httpx.ASGITransport(
            app=RequestContextMiddleware(app),
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(
                "/private", headers={"X-Request-Id": "request-1234"}
            )

    with caplog.at_level(logging.INFO):
        response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"id": "request-user"}
    record = next(r for r in caplog.records if r.getMessage() == "inside request")
    assert record.user_id == "request-user"
    assert record.request_id == "request-1234"
    assert user_id_var.get() is None
    assert request_id_var.get() is None


def test_unhandled_exception_logs_generated_500_response(caplog):
    app = FastAPI()

    @app.get("/failure")
    async def failing_route():
        raise RuntimeError("test failure")

    async def request():
        transport = httpx.ASGITransport(
            app=RequestContextMiddleware(app),
            raise_app_exceptions=False,
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/failure")

    with caplog.at_level(logging.INFO, logger="open_webui.access"):
        response = asyncio.run(request())

    assert response.status_code == 500
    assert response.content == b"Internal Server Error"
    records = [r for r in caplog.records if r.name == "open_webui.access"]
    assert len(records) == 1
    assert records[0].__dict__["http.response.status_code"] == 500
    assert records[0].__dict__["http.response.body.size"] == len(response.content)


@pytest.mark.parametrize(
    "headers, source",
    [
        ([(b"authorization", b"Bearer jwt-token")], "bearer"),
        ([(b"cookie", b"token=jwt-token")], "legacy_cookie"),
        ([(b"authorization", b"Bearer sk-api-key")], "api_key"),
    ],
)
def test_resolved_auth_context_sets_access_identity(monkeypatch, headers, source):
    user = SimpleNamespace(id="resolved-user")
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=SimpleNamespace(ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False)
        )
    )
    scope = _scope(path="/api/chats", headers=headers)
    scope.update(app=app, state={"enable_api_key": True})
    request = Request(scope)

    if source == "api_key":
        monkeypatch.setattr(auth, "get_current_user_by_api_key", lambda _: user)
    else:
        monkeypatch.setattr(auth, "decode_token", lambda _: {"id": user.id})
        monkeypatch.setattr(auth.Users, "get_user_by_id", lambda _: user)
        monkeypatch.setattr(auth.Users, "update_user_last_active_by_id", lambda _: None)

    resolved = auth.resolve_current_user(request)

    assert resolved is not None
    assert resolved.source == source
    assert request.state.auth_user_id == "resolved-user"
