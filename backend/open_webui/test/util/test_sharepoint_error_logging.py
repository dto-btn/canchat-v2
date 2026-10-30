"""OBS-19: SharePoint/Graph error logs keep status, code and IDs, never the raw body."""

import asyncio
import json

import pytest

from mcp_backend.servers import sharepoint_oauth_client as sp
from mcp_backend.servers.sharepoint_oauth_client import (
    SharePointOAuthClient,
    _safe_error_summary,
)

GRAPH_CODE = "itemNotFound"
REQUEST_ID = "req-0b5e2c1d"
CLIENT_REQUEST_ID = "corr-7f3a9e42"


class _FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def text(self):
        return self._body

    async def json(self):
        return json.loads(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response_factory):
        self._response_factory = response_factory

    def _respond(self, *args, **kwargs):
        return self._response_factory()

    request = post = get = _respond

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _graph_error_body(path):
    return json.dumps(
        {
            "error": {
                "code": GRAPH_CODE,
                "message": f"The resource '{path}' could not be found.",
                "innerError": {"date": "2026-10-28T00:00:00", "request-id": REQUEST_ID},
            }
        }
    )


def _entra_error_body(path):
    return json.dumps(
        {
            "error": "invalid_grant",
            "error_description": f"AADSTS50013: Assertion failed for {path}",
            "trace_id": REQUEST_ID,
            "correlation_id": CLIENT_REQUEST_ID,
        }
    )


@pytest.fixture
def canary_path(canary_harness):
    tag = canary_harness.canaries.tag
    return canary_harness.canaries.add(
        "graph path", f"/sites/Restricted-{tag}/Shared Documents/Canary-{tag}.docx"
    )


def _patch_session(monkeypatch, body, status=404):
    headers = {"client-request-id": CLIENT_REQUEST_ID}
    monkeypatch.setattr(
        sp.aiohttp,
        "ClientSession",
        lambda *a, **k: _FakeSession(lambda: _FakeResponse(status, body, headers)),
    )


def _client(delegated=True):
    return SharePointOAuthClient(
        client_id="client-id",
        client_secret="not-a-real-secret",
        tenant_id="tenant-id",
        site_url="https://example.sharepoint.com/sites/test",
        use_delegated_access=delegated,
    )


def _messages(harness):
    return [r.getMessage() for r in harness.records]


def test_safe_error_summary_graph_and_entra_shapes():
    graph = _safe_error_summary(
        404, _graph_error_body("/secret/path"), {"client-request-id": "abc"}
    )
    assert "status=404" in graph
    assert f"code={GRAPH_CODE}" in graph
    assert f"request_id={REQUEST_ID}" in graph
    assert "correlation_id=abc" in graph
    assert "/secret/path" not in graph

    entra = _safe_error_summary(400, _entra_error_body("/secret/path"))
    assert "code=invalid_grant" in entra
    assert f"correlation_id={CLIENT_REQUEST_ID}" in entra
    assert "/secret/path" not in entra

    raw = _safe_error_summary(500, "plain text error mentioning /secret/path")
    assert raw == "status=500 code=None request_id=None correlation_id=None"


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.make_graph_request("/sites/root/drive/items/1", "token"),
        lambda c: c.search_documents("token", "quarterly report"),
        lambda c: c.get_document_content_by_url(
            "token", "https://example.sharepoint.com/doc.docx", "item-1"
        ),
    ],
    ids=["graph_request", "search", "content_by_url"],
)
def test_graph_error_logs_code_not_body(canary_harness, canary_path, monkeypatch, call):
    _patch_session(monkeypatch, _graph_error_body(canary_path))

    ok, result = asyncio.run(call(_client()))

    assert ok is False
    assert result["status_code"] == 404
    messages = _messages(canary_harness)
    assert any(
        f"code={GRAPH_CODE}" in m
        and f"request_id={REQUEST_ID}" in m
        and f"correlation_id={CLIENT_REQUEST_ID}" in m
        for m in messages
    )
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_obo_token("user-assertion"),
        lambda c: c.get_application_token(),
    ],
    ids=["obo", "client_credentials"],
)
def test_token_error_logs_code_not_body(canary_harness, canary_path, monkeypatch, call):
    _patch_session(monkeypatch, _entra_error_body(canary_path), status=400)

    token = asyncio.run(call(_client(delegated=False)))

    assert token is None
    messages = _messages(canary_harness)
    assert any(
        "status=400" in m
        and "code=invalid_grant" in m
        and f"correlation_id={CLIENT_REQUEST_ID}" in m
        for m in messages
    )
    canary_harness.assert_no_canaries()


def test_content_download_error_logs_code_not_body(
    canary_harness, canary_path, monkeypatch
):
    headers = {"client-request-id": CLIENT_REQUEST_ID}
    error_body = _graph_error_body(canary_path)
    responses = iter(
        [
            _FakeResponse(
                200,
                json.dumps({"name": "doc.docx", "parentReference": {"driveId": "d-1"}}),
                headers,
            ),
            _FakeResponse(404, error_body, headers),
        ]
    )
    monkeypatch.setattr(
        sp.aiohttp,
        "ClientSession",
        lambda *a, **k: _FakeSession(lambda: next(responses)),
    )

    ok, result = asyncio.run(
        _client().get_document_content_by_url(
            "token", "https://example.sharepoint.com/doc.docx", "item-1"
        )
    )

    assert ok is False
    assert result == {"error": error_body, "status_code": 404}
    messages = _messages(canary_harness)
    assert any(
        m.startswith("Content download error: ")
        and "status=404" in m
        and f"code={GRAPH_CODE}" in m
        for m in messages
    )
    canary_harness.assert_no_canaries()
