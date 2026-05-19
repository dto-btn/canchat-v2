"""Tests for web search audit logging via container-native structured logs."""

import asyncio
import json
import logging
import time
import uuid
from types import SimpleNamespace

import pytest

from open_webui.routers import retrieval  # noqa: F401
from open_webui.retrieval.web import web_search_logger

from open_webui.retrieval.web.web_search_logger import (
    _hash_query,
    _safe_user_attrs,
    new_web_search_event_id,
    log_web_search_content_load,
    log_web_search_dispatch,
    log_web_search_result,
    web_search_audit_scope,
)

AUDIT_PREFIX = "[web_search_audit] "
SENSITIVE_AUDIT_PREFIX = "[web_search_audit_sensitive] "
LOGGER_NAME = "open_webui.retrieval.web.web_search_logger"


def _extract_audit_records(caplog) -> list[dict]:
    records = []
    for rec in caplog.records:
        if rec.name != LOGGER_NAME:
            continue
        message = rec.getMessage()
        if not message.startswith(AUDIT_PREFIX):
            continue
        payload = message[len(AUDIT_PREFIX) :]
        records.append(json.loads(payload))
    return records


def _extract_sensitive_audit_records(caplog) -> list[dict]:
    records = []
    for rec in caplog.records:
        if rec.name != LOGGER_NAME:
            continue
        message = rec.getMessage()
        if not message.startswith(SENSITIVE_AUDIT_PREFIX):
            continue
        payload = message[len(SENSITIVE_AUDIT_PREFIX) :]
        records.append(json.loads(payload))
    return records


@pytest.fixture
def audit_caplog(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return caplog


@pytest.fixture
def fake_user():
    return SimpleNamespace(
        id="user-abc-123",
        name="Test User",
        email="test.user@example.gc.ca",
        role="user",
    )


@pytest.fixture
def fake_search_results():
    return [
        SimpleNamespace(
            link="https://example.com/page1", title="Page 1", snippet="Snippet 1"
        ),
        SimpleNamespace(
            link="https://example.com/page2", title="Page 2", snippet="Snippet 2"
        ),
        SimpleNamespace(link="https://other.org/page3", title="Page 3", snippet=None),
    ]


class TestSafeUserAttrs:
    def test_with_user(self, fake_user):
        attrs = _safe_user_attrs(fake_user)
        assert attrs["user_id"] == "user-abc-123"
        assert attrs["user_role"] == "user"
        assert attrs["user_email"] == "test.user@example.gc.ca"

    def test_with_none(self):
        attrs = _safe_user_attrs(None)
        assert attrs == {
            "user_id": None,
            "user_role": None,
            "user_email": None,
            "user_name": None,
        }


class TestStructuredAuditLogs:
    def test_dispatch_emits_json_record(self, audit_caplog, fake_user):
        log_web_search_dispatch(
            engine="brave",
            query="audit test query",
            result_count_requested=10,
            domain_filter_list=["example.com"],
            user=fake_user,
        )
        records = _extract_audit_records(audit_caplog)
        record = records[-1]
        assert record["event_type"] == "web_search_dispatch"
        assert record["engine"] == "brave"
        assert record["query"] is None
        assert record["query_hash"] == _hash_query("audit test query")
        assert record["domain_filter_active"] is True
        assert record["user_id"] == "user-abc-123"
        assert record["user_email"] is None
        assert record["user_name"] is None

    def test_dispatch_logs_sensitive_fields_at_debug(self, audit_caplog, fake_user):
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

        log_web_search_dispatch(
            engine="brave",
            query="audit test query",
            result_count_requested=10,
            user=fake_user,
        )

        info_record = _extract_audit_records(audit_caplog)[-1]
        sensitive_record = _extract_sensitive_audit_records(audit_caplog)[-1]

        assert info_record["query"] is None
        assert info_record["user_email"] is None
        assert info_record["user_name"] is None
        assert sensitive_record["event_type"] == "web_search_dispatch_sensitive"
        assert sensitive_record["query"] == "audit test query"
        assert sensitive_record["user_email"] == "test.user@example.gc.ca"
        assert sensitive_record["user_name"] == "Test User"

    def test_dispatch_generates_uuid_event_id(self, audit_caplog):
        ctx = log_web_search_dispatch(
            engine="brave",
            query="event id test",
            result_count_requested=5,
        )
        records = _extract_audit_records(audit_caplog)
        record = records[-1]
        assert record["event_id"] == ctx.event_id
        assert record["event_id"].startswith("wsd_")
        uuid.UUID(record["event_id"][4:])

    def test_result_success_and_domains(self, audit_caplog, fake_search_results):
        ctx = {
            "event_id": "wsd_test123",
            "start_time": time.monotonic() - 0.5,
            "engine": "brave",
        }
        log_web_search_result(dispatch_ctx=ctx, results=fake_search_results)
        records = _extract_audit_records(audit_caplog)
        record = records[-1]
        assert record["event_type"] == "web_search_result"
        assert record["success"] is True
        assert record["result_count"] == 3
        assert record["latency_ms"] >= 400
        assert "example.com" in record["result_domains"]
        assert "other.org" in record["result_domains"]

    def test_result_urls_are_logged_without_query_strings(self, audit_caplog):
        ctx = {
            "event_id": "wsd_sanitized_urls",
            "start_time": time.monotonic() - 0.1,
            "engine": "brave",
        }
        results = [
            SimpleNamespace(
                link="https://example.com/page?token=secret&redirect=internal#frag",
                title="Page 1",
                snippet="Snippet 1",
            )
        ]

        log_web_search_result(dispatch_ctx=ctx, results=results)

        record = _extract_audit_records(audit_caplog)[-1]
        assert record["result_urls"] == ["https://example.com/page"]

    def test_result_failure(self, audit_caplog):
        ctx = {
            "event_id": "wsd_fail456",
            "start_time": time.monotonic(),
            "engine": "brave",
        }
        log_web_search_result(
            dispatch_ctx=ctx, results=[], error=ConnectionError("API unreachable")
        )
        records = _extract_audit_records(audit_caplog)
        record = records[-1]
        assert record["event_type"] == "web_search_result"
        assert record["success"] is False
        assert record["error_type"] == "ConnectionError"

    def test_content_load_record(self, audit_caplog):
        log_web_search_content_load(
            event_id="wscl_test001",
            urls=["https://a.com", "https://b.com"],
            docs_loaded=2,
            latency_ms=350,
            bypass_embedding=False,
            collection_name="web_search_1234-abcd",
        )
        records = _extract_audit_records(audit_caplog)
        record = records[-1]
        assert record["event_type"] == "web_search_content_load"
        assert record["urls_requested_count"] == 2
        assert record["docs_loaded"] == 2
        assert record["collection_name"] == "web_search_1234-abcd"

    def test_content_load_logs_sanitized_urls_at_info_and_full_urls_at_debug(
        self, audit_caplog
    ):
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

        log_web_search_content_load(
            event_id="wscl_sensitive001",
            urls=["https://a.com/path?token=secret#frag"],
            docs_loaded=1,
            latency_ms=100,
            engine="brave",
        )

        info_record = _extract_audit_records(audit_caplog)[-1]
        sensitive_record = _extract_sensitive_audit_records(audit_caplog)[-1]

        assert info_record["event_type"] == "web_search_content_load"
        assert sensitive_record["event_type"] == "web_search_content_load_sensitive"
        assert sensitive_record["urls_requested"] == [
            "https://a.com/path?token=secret#frag"
        ]

    def test_context_manager_logs_failure(self, audit_caplog):
        with pytest.raises(ConnectionError, match="API unreachable"):
            with web_search_audit_scope(
                engine="brave",
                query="failing scoped query",
                result_count_requested=3,
            ):
                raise ConnectionError("API unreachable")

        records = _extract_audit_records(audit_caplog)
        dispatch_record = next(
            r for r in records if r["event_type"] == "web_search_dispatch"
        )
        result_record = next(
            r for r in records if r["event_type"] == "web_search_result"
        )
        assert dispatch_record["event_id"] == result_record["event_id"]
        assert result_record["success"] is False


class TestOpenTelemetryMetrics:
    def test_metrics_are_emitted(self, monkeypatch):
        class FakeCounter:
            def __init__(self):
                self.calls = []

            def add(self, value, attributes=None):
                self.calls.append((value, attributes or {}))

        class FakeHistogram:
            def __init__(self):
                self.calls = []

            def record(self, value, attributes=None):
                self.calls.append((value, attributes or {}))

        dispatch_counter = FakeCounter()
        result_counter = FakeCounter()
        content_counter = FakeCounter()
        result_latency = FakeHistogram()
        result_count = FakeHistogram()
        content_latency = FakeHistogram()

        monkeypatch.setattr(
            web_search_logger,
            "_METRICS_INSTRUMENTS",
            web_search_logger.WebSearchMetricsInstruments(
                dispatch_counter=dispatch_counter,
                result_counter=result_counter,
                result_latency_histogram=result_latency,
                result_count_histogram=result_count,
                content_load_counter=content_counter,
                content_load_latency_histogram=content_latency,
            ),
        )

        ctx = log_web_search_dispatch(
            engine="brave",
            query="metric test",
            result_count_requested=2,
        )
        log_web_search_result(
            dispatch_ctx=ctx,
            results=[SimpleNamespace(link="https://a.com", title="A", snippet="S")],
        )
        log_web_search_content_load(
            event_id=ctx.event_id,
            engine="brave",
            urls=["https://a.com"],
            docs_loaded=1,
            latency_ms=123,
        )

        assert len(dispatch_counter.calls) == 1
        assert len(result_counter.calls) == 1
        assert len(content_counter.calls) == 1
        assert len(result_latency.calls) == 1
        assert len(result_count.calls) == 1
        assert len(content_latency.calls) == 1

        dispatch_attrs = dispatch_counter.calls[0][1]
        assert dispatch_attrs["engine"] == "brave"
        result_attrs = result_counter.calls[0][1]
        assert result_attrs["success"] is True


class TestSearchWebLogging:
    def _make_request(self):
        return SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=SimpleNamespace(
                        RAG_WEB_SEARCH_RESULT_COUNT=5,
                        RAG_WEB_SEARCH_DOMAIN_FILTER_LIST=None,
                        SEARXNG_QUERY_URL="",
                        GOOGLE_PSE_API_KEY="",
                        GOOGLE_PSE_ENGINE_ID="",
                        BRAVE_SEARCH_API_KEY="",
                        KAGI_SEARCH_API_KEY="",
                        MOJEEK_SEARCH_API_KEY="",
                        SERPSTACK_API_KEY="",
                        SERPER_API_KEY="",
                        SERPLY_API_KEY="",
                        TAVILY_API_KEY="",
                        SEARCHAPI_API_KEY="",
                        SEARCHAPI_ENGINE="google",
                        JINA_API_KEY="",
                        BING_SEARCH_V7_SUBSCRIPTION_KEY="",
                        BING_SEARCH_V7_ENDPOINT="",
                    )
                )
            )
        )

    def test_successful_search_logs_dispatch_and_result(
        self, audit_caplog, fake_user, monkeypatch
    ):
        def mock_dispatch(request, engine, query, request_timeout=None):
            return [SimpleNamespace(link="https://example.com", title="T", snippet="S")]

        monkeypatch.setattr(retrieval, "_dispatch_search", mock_dispatch)

        results = retrieval.search_web(
            self._make_request(), "brave", "test logging query", user=fake_user
        )
        assert len(results) == 1

        records = _extract_audit_records(audit_caplog)
        dispatch_record = next(
            r for r in records if r["event_type"] == "web_search_dispatch"
        )
        result_record = next(
            r for r in records if r["event_type"] == "web_search_result"
        )
        assert dispatch_record["query"] is None
        assert dispatch_record["query_hash"] == _hash_query("test logging query")
        assert result_record["success"] is True
        assert result_record["event_id"] == dispatch_record["event_id"]

    def test_failed_search_logs_dispatch_and_error(
        self, audit_caplog, fake_user, monkeypatch
    ):
        def mock_dispatch(request, engine, query, request_timeout=None):
            raise ConnectionError("API unreachable")

        monkeypatch.setattr(retrieval, "_dispatch_search", mock_dispatch)

        with pytest.raises(ConnectionError, match="API unreachable"):
            retrieval.search_web(
                self._make_request(), "brave", "failing query", user=fake_user
            )

        records = _extract_audit_records(audit_caplog)
        dispatch_record = next(
            r for r in records if r["event_type"] == "web_search_dispatch"
        )
        result_record = next(
            r for r in records if r["event_type"] == "web_search_result"
        )
        assert dispatch_record["engine"] == "brave"
        assert result_record["success"] is False


class TestProcessWebSearchLogging:
    def test_content_load_logged_in_pipeline(
        self, audit_caplog, fake_user, monkeypatch
    ):
        def fake_dispatch(request, engine, query, request_timeout=None):
            return [
                SimpleNamespace(
                    link="https://example.com/page1", title="T", snippet="S"
                )
            ]

        class FakeLoader:
            def load(self):
                return [retrieval.Document(page_content="hello", metadata={})]

            def lazy_load(self):
                yield retrieval.Document(page_content="hello", metadata={})

        def fake_get_web_loader(
            urls, verify_ssl=True, requests_per_second=2, request_timeout=None
        ):
            return FakeLoader()

        monkeypatch.setattr(retrieval.RAG_WEB_SEARCH_TOTAL_TIMEOUT, "value", 30)
        monkeypatch.setattr(retrieval, "_dispatch_search", fake_dispatch)
        monkeypatch.setattr(retrieval, "get_web_loader", fake_get_web_loader)

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=SimpleNamespace(
                        RAG_WEB_SEARCH_ENGINE="brave",
                        RAG_WEB_SEARCH_RESULT_COUNT=5,
                        RAG_WEB_SEARCH_DOMAIN_FILTER_LIST=None,
                        ENABLE_RAG_WEB_LOADER_SSL_VERIFICATION=True,
                        RAG_WEB_SEARCH_CONCURRENT_REQUESTS=2,
                        BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=True,
                    )
                )
            )
        )

        result = asyncio.run(
            retrieval.process_web_search(
                request,
                retrieval.SearchForm(query="pipeline logging test"),
                user=fake_user,
            )
        )
        assert result["status"] is True
        assert result["loaded_count"] == 1

        records = _extract_audit_records(audit_caplog)
        dispatch_record = next(
            r for r in records if r["event_type"] == "web_search_dispatch"
        )
        result_record = next(
            r for r in records if r["event_type"] == "web_search_result"
        )
        content_load = [
            r for r in records if r["event_type"] == "web_search_content_load"
        ]
        assert len(content_load) >= 1
        assert dispatch_record["event_id"] == result_record["event_id"]
        assert content_load[-1]["event_id"] == dispatch_record["event_id"]
        assert content_load[-1]["docs_loaded"] == 1
        assert content_load[-1]["bypass_embedding"] is True


class TestAuditPayloadFormat:
    def test_new_event_id_helper_returns_full_uuid(self):
        event_id = new_web_search_event_id()
        assert event_id.startswith("wsd_")
        uuid.UUID(event_id[4:])

    def test_records_are_valid_json(self, audit_caplog, fake_user):
        ctx = log_web_search_dispatch(
            engine="brave",
            query="json validity test",
            result_count_requested=5,
            user=fake_user,
        )
        log_web_search_result(
            dispatch_ctx=ctx,
            results=[SimpleNamespace(link="https://a.com", title="A", snippet="S")],
        )
        log_web_search_content_load(
            event_id="wscl_jsontest",
            urls=["https://a.com"],
            docs_loaded=1,
            latency_ms=100,
        )

        records = _extract_audit_records(audit_caplog)
        assert len(records) >= 3
        for record in records[-3:]:
            assert "event_type" in record
            assert "timestamp" in record
            assert "event_id" in record

    def test_dispatch_contains_leakage_analysis_attributes(
        self, audit_caplog, fake_user
    ):
        sensitive_query = "What is employee John Smith's salary at Department X?"
        log_web_search_dispatch(
            engine="brave",
            query=sensitive_query,
            result_count_requested=5,
            user=fake_user,
        )
        record = _extract_audit_records(audit_caplog)[-1]
        assert record["query"] is None
        assert record["query_hash"] == _hash_query(sensitive_query)
        assert record["user_email"] is None
