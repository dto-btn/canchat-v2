import asyncio
import time
from types import SimpleNamespace

import pytest
import requests
from bs4 import BeautifulSoup

from open_webui.retrieval.web import brave
from open_webui.retrieval.web import google_pse
from open_webui.retrieval.web import utils as web_utils
from open_webui.retrieval.web import serper
from open_webui.retrieval.web.utils import SafeWebBaseLoader
from open_webui.routers import retrieval


def test_brave_timeout_path(monkeypatch):
    """Brave search should propagate request timeouts and use configured timeout seconds."""
    called = {}

    def fake_request(
        method, url, headers=None, params=None, data=None, json=None, timeout=None
    ):
        called["timeout"] = timeout
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(web_utils.RAG_WEB_SEARCH_REQUEST_TIMEOUT, "value", 9)
    monkeypatch.setattr(web_utils.requests, "request", fake_request)

    with pytest.raises(requests.exceptions.Timeout):
        brave.search_brave(
            api_key="k",
            query="timeout query",
            count=3,
        )

    # Ensure the helper used the configured timeout value.
    assert called["timeout"] == 9


def test_google_pse_timeout_path(monkeypatch):
    """Google PSE should propagate request timeouts and use configured timeout seconds."""
    called = {}

    # Simulate provider timeout and capture timeout seconds passed to requests.get
    def fake_request(
        method, url, headers=None, params=None, data=None, json=None, timeout=None
    ):
        called["timeout"] = timeout
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(web_utils.RAG_WEB_SEARCH_REQUEST_TIMEOUT, "value", 7)
    monkeypatch.setattr(web_utils.requests, "request", fake_request)

    with pytest.raises(requests.exceptions.Timeout):
        google_pse.search_google_pse(
            api_key="test",
            search_engine_id="cx",
            query="timeout query",
            count=3,
        )

    assert called["timeout"] == 7


def test_serper_timeout_path(monkeypatch):
    """Serper (POST provider) should propagate request timeouts and use configured timeout seconds."""
    called = {}

    def fake_request(
        method, url, headers=None, params=None, data=None, json=None, timeout=None
    ):
        called["method"] = method
        called["timeout"] = timeout
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(web_utils.RAG_WEB_SEARCH_REQUEST_TIMEOUT, "value", 11)
    monkeypatch.setattr(web_utils.requests, "request", fake_request)

    with pytest.raises(requests.exceptions.Timeout):
        serper.search_serper(
            api_key="k",
            query="timeout query",
            count=3,
        )

    assert called["method"] == "POST"
    assert called["timeout"] == 11


def test_url_fetch_timeout_continue_behavior(monkeypatch):
    """SafeWebBaseLoader should skip timed-out URLs and continue loading remaining URLs."""
    loader = SafeWebBaseLoader(["https://slow.example", "https://ok.example"])

    # First URL times out, second URL succeeds
    def fake_scrape(url, parser=None, bs_kwargs=None):
        if "slow.example" in url:
            raise requests.exceptions.Timeout("timed out")
        return BeautifulSoup(
            "<html><title>OK</title><body>Hello</body></html>", "html.parser"
        )

    monkeypatch.setattr(loader, "_scrape", fake_scrape)

    docs = list(loader.lazy_load())
    # Continue-on-failure should keep the successful document only
    assert len(docs) == 1
    assert docs[0].metadata["source"] == "https://ok.example"
    assert "Hello" in docs[0].page_content


def test_total_timeout_enforcement(monkeypatch):
    """process_web_search should return HTTP 408 when the total web-search budget is exceeded."""

    # Simulate search work that outlives the limit
    def slow_search_web(
        request, engine, query, request_timeout=None, user=None, audit_event_id=None
    ):
        time.sleep(2)
        return []

    monkeypatch.setattr(retrieval.RAG_WEB_SEARCH_TOTAL_TIMEOUT, "value", 1)
    monkeypatch.setattr(retrieval, "search_web", slow_search_web)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(RAG_WEB_SEARCH_ENGINE="brave"))
        )
    )
    form_data = retrieval.SearchForm(query="slow query")

    with pytest.raises(retrieval.HTTPException) as exc:
        asyncio.run(retrieval.process_web_search(request, form_data, user=None))

    assert exc.value.status_code == 408
    assert "timed out after 1 seconds" in exc.value.detail


def test_http_helper_non_json_response_raises_request_exception(monkeypatch):
    """Shared HTTP helper should normalize invalid JSON responses as RequestException."""

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("invalid json")

    # HTTP succeeds but body is not valid JSON
    def fake_request(
        method, url, headers=None, params=None, data=None, json=None, timeout=None
    ):
        return FakeResponse()

    monkeypatch.setattr(web_utils.requests, "request", fake_request)

    with pytest.raises(
        requests.exceptions.RequestException, match="returned invalid JSON"
    ):
        web_utils.get_json_with_timeout(
            "https://provider.example",
            provider_name="Test Provider",
        )


def test_remaining_timeout_propagates_to_search_and_loader(monkeypatch):
    """process_web_search should pass remaining timeout to search and loader."""
    captured = {}

    # Capture timeout passed to provider search function
    def fake_search_web(
        request, engine, query, request_timeout=None, user=None, audit_event_id=None
    ):
        captured["search_timeout"] = request_timeout
        return [SimpleNamespace(link="https://ok.example")]

    class FakeLoader:
        def load(self):
            return [retrieval.Document(page_content="hello", metadata={})]

        def lazy_load(self):
            return iter(self.load())

    # Capture timeout passed to web loader fetch path
    def fake_get_web_loader(
        urls, verify_ssl=True, requests_per_second=2, request_timeout=None
    ):
        captured["loader_timeout"] = request_timeout
        captured["urls"] = urls
        return FakeLoader()

    monkeypatch.setattr(retrieval.RAG_WEB_SEARCH_TOTAL_TIMEOUT, "value", 5)
    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(retrieval, "get_web_loader", fake_get_web_loader)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    RAG_WEB_SEARCH_ENGINE="brave",
                    ENABLE_RAG_WEB_LOADER_SSL_VERIFICATION=True,
                    RAG_WEB_SEARCH_CONCURRENT_REQUESTS=2,
                    BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=True,
                )
            )
        )
    )

    form_data = retrieval.SearchForm(query="budget propagation")
    result = asyncio.run(retrieval.process_web_search(request, form_data, user=None))

    assert result["status"] is True
    assert result["loaded_count"] == 1
    assert captured["urls"] == ["https://ok.example"]
    assert isinstance(captured["search_timeout"], int)
    assert isinstance(captured["loader_timeout"], int)
    assert 1 <= captured["search_timeout"] <= 5
    assert 1 <= captured["loader_timeout"] <= 5
