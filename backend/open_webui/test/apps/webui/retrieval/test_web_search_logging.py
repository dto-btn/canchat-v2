"""Tests for web search audit logging via container-native structured logs."""

import asyncio
import inspect
import json
import logging
import time
import uuid
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.metrics.service import MetricsService
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
        assert "user_email" not in attrs
        assert "user_name" not in attrs

    def test_with_none(self):
        attrs = _safe_user_attrs(None)
        assert attrs == {
            "user_id": None,
            "user_role": None,
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

    def test_dispatch_omits_sensitive_fields_at_debug(self, audit_caplog, fake_user):
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

        log_web_search_dispatch(
            engine="brave",
            query="audit test query",
            result_count_requested=10,
            user=fake_user,
        )

        info_record = _extract_audit_records(audit_caplog)[-1]

        assert info_record["query"] is None
        assert info_record["user_email"] is None
        assert info_record["user_name"] is None
        assert _extract_sensitive_audit_records(audit_caplog) == []

    def test_debug_output_excludes_sensitive_canaries(
        self, audit_caplog, canary_harness
    ):
        audit_caplog.set_level(logging.DEBUG)
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
        canaries = canary_harness.canaries
        query, email, name = canaries.prompt, canaries.email, canaries.name
        sensitive_text = f"{query} {email} {name}"
        urls = [
            f"https://{email}:{canaries.password}@example.com/{query}/{name}"
            f"?q={query}&key={canaries.api_key}#{canaries.token}",
            f"https://[invalid/{sensitive_text}",
            f"/{sensitive_text}",
        ]
        ctx = log_web_search_dispatch(
            engine="brave",
            query=query,
            result_count_requested=3,
            user=SimpleNamespace(id="user-id", role="user", email=email, name=name),
        )
        log_web_search_result(
            dispatch_ctx=ctx,
            results=[SimpleNamespace(link=url) for url in urls],
            error=ConnectionError(sensitive_text),
        )
        log_web_search_content_load(
            event_id=ctx.event_id,
            urls=urls,
            docs_loaded=0,
            latency_ms=1,
            error=RuntimeError(sensitive_text),
        )

        records = _extract_audit_records(audit_caplog)
        assert len(records) == 3
        assert records[1]["result_urls"] == ["https://example.com"]
        assert records[1]["result_domains"] == ["example.com"]
        assert records[1]["error_message"] is None
        assert records[2]["error_message"] is None
        canary_harness.assert_no_canaries()

    @pytest.mark.parametrize(
        "failure_site",
        ["serialization", "metric", "dispatch", "result", "content_load"],
    )
    def test_internal_failures_omit_exception_details(
        self, audit_caplog, canary_harness, monkeypatch, failure_site
    ):
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
        canaries = canary_harness.canaries
        sensitive_text = f"{canaries.prompt} {canaries.email} {canaries.token}"

        def fail(*args, **kwargs):
            raise RuntimeError(sensitive_text)

        ctx = web_search_logger.WebSearchAuditContext(
            event_id="wsd_failure", engine="brave"
        )
        if failure_site == "serialization":
            # Patch only this module's json; the harness sinks need the real one.
            monkeypatch.setattr(web_search_logger, "json", SimpleNamespace(dumps=fail))
            web_search_logger._emit_audit_record({"event_type": "test"})
        elif failure_site == "metric":
            failing_service = SimpleNamespace(
                has_instrument=lambda name: True, record=fail
            )
            monkeypatch.setattr(
                web_search_logger, "get_metrics_service", lambda: failing_service
            )
            web_search_logger._record_metric("canchat.web_search.results", 1, {})
        else:
            monkeypatch.setattr(web_search_logger, "_emit_audit_record", fail)
            if failure_site == "dispatch":
                log_web_search_dispatch(
                    engine="brave", query=sensitive_text, result_count_requested=1
                )
            elif failure_site == "result":
                log_web_search_result(dispatch_ctx=ctx, results=[])
                assert ctx.result_logged is True
            else:
                log_web_search_content_load(
                    event_id=ctx.event_id, urls=[], docs_loaded=0, latency_ms=1
                )

        assert len(audit_caplog.records) == 1
        assert audit_caplog.records[0].exc_info is None
        canary_harness.assert_no_canaries()

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "https://example.com/path;params?q=secret#fragment",
                ["https://example.com"],
            ),
            ("https://user:secret@example.com:8443/path", ["https://example.com:8443"]),
            ("https://[::1]:8443/path", ["https://[::1]:8443"]),
            ("https://[invalid/secret", []),
            ("https://example.com:secret/path", []),
            ("/relative/secret", []),
            ("mailto:secret@example.com", []),
        ],
    )
    def test_url_sanitization_fails_closed(self, url, expected):
        assert web_search_logger._sanitize_urls([url], 20) == expected

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
        assert record["result_urls"] == ["https://example.com"]

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

    def test_content_load_omits_full_urls_at_debug(self, audit_caplog):
        audit_caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

        log_web_search_content_load(
            event_id="wscl_sensitive001",
            urls=["https://a.com/path?token=secret#frag"],
            docs_loaded=1,
            latency_ms=100,
            engine="brave",
        )

        info_record = _extract_audit_records(audit_caplog)[-1]

        assert info_record["event_type"] == "web_search_content_load"
        assert _extract_sensitive_audit_records(audit_caplog) == []
        assert "token=secret" not in audit_caplog.text

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


@pytest.fixture
def metric_reader(monkeypatch):
    """Route web-search metrics into a strict, in-memory ``MetricsService``."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    service = MetricsService(
        provider.get_meter("test"), DEFAULT_METRIC_DEFINITIONS, strict=True
    )
    monkeypatch.setattr(web_search_logger, "get_metrics_service", lambda: service)
    yield reader
    provider.shutdown()


def _points(reader) -> dict:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        metric.name: list(metric.data.data_points)
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


class TestMetricsServiceRecording:
    @pytest.mark.parametrize(
        "error", [None, ConnectionError("sensitive provider detail")]
    )
    def test_metrics_go_through_metrics_service(self, metric_reader, error):
        ctx = log_web_search_dispatch(
            engine="brave",
            query="metric test",
            result_count_requested=2,
            domain_filter_list=["example.com"],
        )
        log_web_search_result(
            dispatch_ctx=ctx,
            results=[SimpleNamespace(link="https://a.com", title="A", snippet="S")],
            error=error,
        )
        log_web_search_content_load(
            event_id=ctx.event_id,
            engine="brave",
            urls=["https://a.com"],
            docs_loaded=1,
            latency_ms=1500,
            error=error,
        )

        points = _points(metric_reader)
        outcome_attrs = {
            "provider": "brave",
            "outcome": "error" if error else "success",
        }
        if error:
            outcome_attrs["error.type"] = "ConnectionError"

        [duration] = points["canchat.web_search.request.duration"]
        assert dict(duration.attributes) == {
            **outcome_attrs,
            "domain_filter_active": True,
        }
        assert duration.count == 1
        assert 0 <= duration.sum < 5

        [result_count] = points["canchat.web_search.results"]
        assert dict(result_count.attributes) == outcome_attrs
        assert result_count.sum == 1

        [content_load] = points["canchat.web_search.content_load.duration"]
        assert dict(content_load.attributes) == {
            **outcome_attrs,
            "bypass_embedding": False,
        }
        assert content_load.sum == pytest.approx(1.5)

        exported = json.dumps(
            [dict(p.attributes) for series in points.values() for p in series]
        )
        assert "sensitive provider detail" not in exported
        assert "metric test" not in exported

    def test_no_raw_instruments_bypass_metrics_service(self):
        source = inspect.getsource(web_search_logger)
        assert "create_counter" not in source
        assert "create_histogram" not in source
        assert "instrumentation" not in source

    def test_definitions_missing_from_service_are_skipped(
        self, monkeypatch, audit_caplog
    ):
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
        service = MetricsService(provider.get_meter("test"), [], strict=True)
        monkeypatch.setattr(web_search_logger, "get_metrics_service", lambda: service)

        ctx = log_web_search_dispatch(
            engine="brave", query="custom definitions", result_count_requested=1
        )
        log_web_search_result(dispatch_ctx=ctx, results=[])

        assert _points(reader) == {}
        assert audit_caplog.records[-1].levelno == logging.INFO
        provider.shutdown()

    def test_no_service_is_a_no_op(self, monkeypatch, audit_caplog):
        monkeypatch.setattr(web_search_logger, "get_metrics_service", lambda: None)
        ctx = log_web_search_dispatch(
            engine="brave", query="no service", result_count_requested=1
        )
        log_web_search_result(dispatch_ctx=ctx, results=[])
        assert len(_extract_audit_records(audit_caplog)) == 2


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
