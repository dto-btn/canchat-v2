"""OBS-20: tracing policy, auto-instrumentation and in-memory span export."""

import asyncio
import io
import json
import logging
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import aiohttp
import httpx
import pytest
import redis
import requests
from aiohttp import web
from fastapi import FastAPI, WebSocket
from opentelemetry import baggage, context, propagate, trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
)
from opentelemetry.util.http import parse_excluded_urls
import sqlalchemy
from sqlalchemy import text
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from open_webui.observability import tracing
from open_webui.observability.logging_config import JsonFormatter
from open_webui.observability.middleware import RequestContextMiddleware
from open_webui.observability.providers import (
    OTelSettings,
    create_providers,
    load_settings,
)

ALLOWED_HOST = "localhost"
REQUEST_ID = "request-obs20-0001"
# TraceIdRatioBased compares the low 64 bits of the trace ID with the bound;
# remote parents use a random draw instead (see _RemoteParentSampler).
IN_RATIO_TRACE_ID = "4bf92f3577b34da60000000000000001"
OUT_OF_RATIO_TRACE_ID = "4bf92f3577b34da6ffffffffffffffff"
PARENT_SPAN_ID = "00f067aa0ba902b7"
SECRET = "secret-value-obs20"


def _settings(**overrides) -> OTelSettings:
    values = {
        "enabled": False,
        "service_name": "test-service",
        "service_version": "test",
        "environment": "test",
        "endpoint": "http://collector:4318",
        "meter_name": "test-meter",
        "metric_export_interval_ms": 15000,
        "traces_sampler_ratio": 1.0,
    }
    values.update(overrides)
    return OTelSettings(**values)


class Tracing:
    def __init__(self, ratio: float = 1.0):
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.bundle = create_providers(
            _settings(traces_sampler_ratio=ratio),
            metric_readers=[self.reader],
            span_exporters=[self.exporter],
        )
        self.tracer = self.bundle.tracer_provider.get_tracer("test")

    def app(self, fastapi_app: FastAPI, include_healthchecks: bool = False):
        assert tracing.instrument_app(
            fastapi_app,
            tracer_provider=self.bundle.tracer_provider,
            meter_provider=self.bundle.meter_provider,
            include_healthchecks=include_healthchecks,
        )
        return RequestContextMiddleware(fastapi_app)

    def instrument_libraries(self):
        assert tracing.instrument_libraries(
            tracer_provider=self.bundle.tracer_provider,
            meter_provider=self.bundle.meter_provider,
        )

    def spans(self, kind: SpanKind | None = None) -> list[ReadableSpan]:
        return [
            span
            for span in self.exporter.get_finished_spans()
            if kind is None or span.kind == kind
        ]

    def metric(self, name: str):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        return metric
        return None


@pytest.fixture
def otel(request):
    previous_textmap = propagate.get_global_textmap()
    tracing.configure_propagation((ALLOWED_HOST,))
    ratio = getattr(request, "param", 1.0)
    harness = Tracing(ratio)
    try:
        yield harness
    finally:
        tracing.uninstrument_libraries()
        harness.bundle.shutdown()
        tracing.configure_propagation(())
        propagate.set_global_textmap(previous_textmap)


def _request(app, method: str, path: str, headers=None) -> httpx.Response:
    async def call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(call())


def _all_attribute_values(span: ReadableSpan) -> str:
    values = [str(value) for value in span.attributes.values()]
    for event in span.events:
        values.extend(str(value) for value in event.attributes.values())
    values.append(str(span.status.description))
    return " ".join(values)


########################################
# Settings and semantic conventions
########################################


def test_tracing_settings_defaults():
    assert load_settings({"ENV": "prod"}).traces_sampler_ratio == 0.1
    assert load_settings({"ENV": "dev", "DOCKER": "true"}).traces_sampler_ratio == 0.1
    assert load_settings({"ENV": "dev"}).traces_sampler_ratio == 1.0
    settings = load_settings({"ENV": "prod"})
    assert settings.traces_healthchecks is False
    assert settings.propagation_allowed_hosts == ()


def test_tracing_settings_are_parsed():
    settings = load_settings(
        {
            "ENV": "prod",
            "OTEL_TRACES_SAMPLER_RATIO": " 0.25 ",
            "OTEL_TRACES_HEALTHCHECKS": "TRUE",
            "OTEL_PROPAGATION_ALLOWED_HOSTS": (
                " Pipelines.Internal. , *.svc.cluster.local,10.0.0.5,,pipelines.internal"
            ),
        }
    )
    assert settings.traces_sampler_ratio == 0.25
    assert settings.traces_healthchecks is True
    assert settings.propagation_allowed_hosts == (
        "pipelines.internal",
        "*.svc.cluster.local",
        "10.0.0.5",
    )


@pytest.mark.parametrize(
    "values, message",
    [
        ({"OTEL_TRACES_SAMPLER_RATIO": "often"}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_SAMPLER_RATIO": ""}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_SAMPLER_RATIO": "nan"}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_SAMPLER_RATIO": "inf"}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_SAMPLER_RATIO": "1.5"}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_SAMPLER_RATIO": "-0.1"}, "OTEL_TRACES_SAMPLER_RATIO"),
        ({"OTEL_TRACES_HEALTHCHECKS": "yes"}, "OTEL_TRACES_HEALTHCHECKS"),
        ({"OTEL_PROPAGATION_ALLOWED_HOSTS": "*"}, "OTEL_PROPAGATION_ALLOWED_HOSTS"),
        ({"OTEL_PROPAGATION_ALLOWED_HOSTS": "*.com"}, "OTEL_PROPAGATION_ALLOWED_HOSTS"),
        (
            {"OTEL_PROPAGATION_ALLOWED_HOSTS": "bad host"},
            "OTEL_PROPAGATION_ALLOWED_HOSTS",
        ),
        (
            {"OTEL_PROPAGATION_ALLOWED_HOSTS": "https://api.example"},
            "OTEL_PROPAGATION_ALLOWED_HOSTS",
        ),
    ],
)
def test_invalid_tracing_settings_fail_clearly(values, message):
    with pytest.raises(ValueError, match=message):
        load_settings({"ENV": "prod", **values})


def test_semconv_opt_in_defaults_to_stable_http():
    environ = {}
    assert tracing.apply_semconv_opt_in(environ) == "http"
    assert environ == {"OTEL_SEMCONV_STABILITY_OPT_IN": "http"}
    assert (
        tracing.apply_semconv_opt_in(
            {"OTEL_SEMCONV_STABILITY_OPT_IN": "database, http"}
        )
        == "database, http"
    )


@pytest.mark.parametrize("value", ["", "database", "http/dup", "http,http/dup"])
def test_semconv_opt_in_rejects_old_or_duplicated_http(value):
    with pytest.raises(ValueError, match="OTEL_SEMCONV_STABILITY_OPT_IN"):
        tracing.apply_semconv_opt_in({"OTEL_SEMCONV_STABILITY_OPT_IN": value})


def test_process_uses_stable_http_semconv():
    tracing._require_stable_http_semconv()


def test_instrumentation_is_skipped_when_export_is_disabled(monkeypatch):
    monkeypatch.setattr(
        tracing,
        "_bundle",
        lambda: SimpleNamespace(export_enabled=False, settings=None),
    )
    app = FastAPI()

    assert tracing.instrument_libraries() is False
    assert tracing.instrument_app(app) is False
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


########################################
# Server spans and metrics
########################################


def _items_app(otel: Tracing):
    app = FastAPI()

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: str):
        logging.getLogger("open_webui.test.obs20").info("handling item")
        return {"id": item_id}

    @app.get("/api/v1/fail")
    async def fail():
        raise RuntimeError(f"failure with {SECRET}")

    return otel.app(app)


def test_route_produces_one_bounded_server_span(otel):
    app = _items_app(otel)

    response = _request(
        app,
        "GET",
        f"/api/v1/items/{SECRET}?token={SECRET}",
        headers={"x-request-id": REQUEST_ID, "user-agent": SECRET},
    )

    assert response.status_code == 200
    spans = otel.spans()
    # Per-chunk ASGI send/receive spans are disabled.
    assert len(spans) == 1
    span = spans[0]
    assert span.kind == SpanKind.SERVER
    assert span.name == "GET /api/v1/items/{item_id}"
    assert span.attributes["http.route"] == "/api/v1/items/{item_id}"
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["canchat.request.id"] == REQUEST_ID
    for key in ("url.path", "url.query", "client.address", "user_agent.original"):
        assert key not in span.attributes
    assert SECRET not in _all_attribute_values(span)


def test_failed_route_keeps_error_type_but_not_exception_message(otel):
    app = _items_app(otel)

    with pytest.raises(RuntimeError):
        _request(app, "GET", "/api/v1/fail")

    span = otel.spans(SpanKind.SERVER)[0]
    assert span.name == "GET /api/v1/fail"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description is None
    exception_events = [event for event in span.events if event.name == "exception"]
    assert exception_events
    assert exception_events[0].attributes["exception.type"] == "RuntimeError"
    assert "exception.message" not in exception_events[0].attributes
    assert "exception.stacktrace" not in exception_events[0].attributes
    assert SECRET not in _all_attribute_values(span)


def test_server_duration_metric_is_stable_and_in_seconds(otel):
    app = _items_app(otel)

    for item_id in ("1", "2", "3"):
        assert _request(app, "GET", f"/api/v1/items/{item_id}").status_code == 200

    metric = otel.metric("http.server.request.duration")
    assert metric is not None
    assert metric.unit == "s"
    assert otel.metric("http.server.duration") is None
    points = list(metric.data.data_points)
    # One series for the route template, not one per item id.
    assert len(points) == 1
    point = points[0]
    assert point.count == 3
    assert point.attributes["http.route"] == "/api/v1/items/{item_id}"
    assert point.attributes["http.request.method"] == "GET"
    assert point.attributes["http.response.status_code"] == 200
    assert set(point.attributes) <= {
        "http.request.method",
        "http.response.status_code",
        "http.route",
        "url.scheme",
        "network.protocol.name",
        "network.protocol.version",
        "error.type",
    }
    assert point.sum < 60


@pytest.mark.parametrize("include_healthchecks", [False, True])
def test_health_checks_are_traced_only_when_enabled(otel, include_healthchecks):
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": True}

    @app.get("/health/db")
    async def health_db():
        return {"status": True}

    wrapped = otel.app(app, include_healthchecks=include_healthchecks)

    assert _request(wrapped, "GET", "/health").status_code == 200
    assert _request(wrapped, "GET", "/health/db").status_code == 200

    names = sorted(span.name for span in otel.spans())
    if include_healthchecks:
        assert names == ["GET /health", "GET /health/db"]
    else:
        assert names == []
        assert otel.metric("http.server.request.duration") is None


def test_socketio_and_websocket_connections_are_not_traced(otel):
    app = FastAPI()

    async def socketio(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    app.mount("/ws", socketio)

    @app.websocket("/live")
    async def live(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    @app.get("/wsx")
    async def not_socketio():
        return {}

    wrapped = otel.app(app)

    assert (
        _request(wrapped, "GET", "/ws/socket.io/?EIO=4&transport=polling").status_code
        == 200
    )
    with TestClient(wrapped).websocket_connect("/live") as websocket:
        assert websocket.receive_text() == "hello"
    assert otel.spans() == []

    assert _request(wrapped, "GET", "/wsx").status_code == 200
    assert [span.name for span in otel.spans()] == ["GET /wsx"]


@pytest.mark.parametrize(
    "url, excluded",
    [
        ("http://canchat/health", True),
        ("https://canchat/health/ready", True),
        ("http://canchat/api/health", False),
        ("http://canchat/ws/socket.io/", True),
        ("http://canchat/ws", True),
        ("http://canchat/wsx", False),
        ("ws://canchat/anything", True),
        ("wss://canchat/anything", True),
        ("http://canchat/api/v1/chats", False),
    ],
)
def test_server_exclusions(url, excluded):
    assert (
        parse_excluded_urls(
            tracing.server_excluded_urls(include_healthchecks=False)
        ).url_disabled(url)
        is excluded
    )


def test_health_exclusion_is_removed_when_enabled():
    excluded = parse_excluded_urls(tracing.server_excluded_urls(True))
    assert not excluded.url_disabled("http://canchat/health")
    assert excluded.url_disabled("http://canchat/ws/socket.io/")


########################################
# Log correlation
########################################


def test_logs_carry_trace_and_span_ids(otel):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter({}))
    loggers = [
        logging.getLogger("open_webui.access"),
        logging.getLogger("open_webui.test.obs20"),
    ]
    previous_levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    try:
        app = _items_app(otel)
        assert _request(app, "GET", "/api/v1/items/1").status_code == 200
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.removeHandler(handler)
            logger.setLevel(level)

    span = otel.spans(SpanKind.SERVER)[0]
    records = {
        record["logger"]: record
        for record in map(json.loads, stream.getvalue().splitlines())
    }
    for name in ("open_webui.test.obs20", "open_webui.access"):
        assert records[name]["trace_id"] == format(span.context.trace_id, "032x")
    # The access record is written after the server span ends and points at it.
    assert records["open_webui.access"]["span_id"] == format(
        span.context.span_id, "016x"
    )
    assert records["open_webui.access"]["event"] == "http.request.completed"


def test_formatter_omits_trace_fields_without_active_span():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    fields = json.loads(JsonFormatter({}).format(record))
    assert "trace_id" not in fields and "span_id" not in fields


########################################
# Sampling and incoming traceparent
########################################


def _traceparent(trace_id: str, flags: str = "01", version: str = "00") -> str:
    return f"{version}-{trace_id}-{PARENT_SPAN_ID}-{flags}"


@pytest.mark.parametrize("otel", [0.5], indirect=True)
def test_missing_traceparent_starts_new_root_trace(otel):
    app = _items_app(otel)
    for _ in range(64):
        assert _request(app, "GET", "/api/v1/items/1").status_code == 200

    spans = otel.spans()
    # Ratio sampling: some but not all of 64 new traces are kept.
    assert 0 < len(spans) < 64
    assert all(span.parent is None for span in spans)


@pytest.mark.parametrize(
    "header",
    [
        "garbage",
        "",
        _traceparent("0" * 32),
        f"00-{IN_RATIO_TRACE_ID}-{'0' * 16}-01",
        _traceparent(IN_RATIO_TRACE_ID, version="ff"),
        _traceparent(IN_RATIO_TRACE_ID.upper()),
        _traceparent(IN_RATIO_TRACE_ID)[:-1],
        _traceparent(IN_RATIO_TRACE_ID) + "-extra\x00",
        "00-" + "a" * 4096,
    ],
)
def test_malformed_traceparent_is_ignored(otel, header):
    app = _items_app(otel)

    response = _request(app, "GET", "/api/v1/items/1", headers={"traceparent": header})

    assert response.status_code == 200
    span = otel.spans()[0]
    assert span.parent is None
    assert format(span.context.trace_id, "032x") != IN_RATIO_TRACE_ID.lower()


def test_valid_traceparent_continues_trace(otel):
    app = _items_app(otel)

    _request(
        app,
        "GET",
        "/api/v1/items/1",
        headers={"traceparent": _traceparent(IN_RATIO_TRACE_ID)},
    )

    span = otel.spans()[0]
    assert format(span.context.trace_id, "032x") == IN_RATIO_TRACE_ID
    assert format(span.parent.span_id, "016x") == PARENT_SPAN_ID
    assert span.parent.is_remote


@pytest.mark.parametrize("otel", [0.5], indirect=True)
def test_sampled_flag_cannot_force_sampling(otel, monkeypatch):
    monkeypatch.setattr(tracing, "_remote_sampling_draw", lambda: 0.9)
    app = _items_app(otel)

    _request(
        app,
        "GET",
        "/api/v1/items/1",
        headers={"traceparent": _traceparent(OUT_OF_RATIO_TRACE_ID, flags="01")},
    )

    assert otel.spans() == []


@pytest.mark.parametrize("otel", [0.5], indirect=True)
def test_unsampled_flag_cannot_opt_out_of_sampling(otel, monkeypatch):
    monkeypatch.setattr(tracing, "_remote_sampling_draw", lambda: 0.1)
    app = _items_app(otel)

    _request(
        app,
        "GET",
        "/api/v1/items/1",
        headers={"traceparent": _traceparent(IN_RATIO_TRACE_ID, flags="00")},
    )

    assert len(otel.spans()) == 1


def test_chosen_trace_id_cannot_force_sampling():
    sampler = tracing.build_sampler(0.01)
    parent = trace.set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=int(IN_RATIO_TRACE_ID, 16),
                span_id=int(PARENT_SPAN_ID, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    )

    sampled = sum(
        sampler.should_sample(
            parent, int(IN_RATIO_TRACE_ID, 16), "GET", SpanKind.SERVER
        ).decision
        == Decision.RECORD_AND_SAMPLE
        for _ in range(1000)
    )

    # Binomial(1000, 0.01): mean 10; 1000 would mean the trace ID decided.
    assert sampled < 100


@pytest.mark.parametrize("otel", [0.0], indirect=True)
def test_zero_ratio_records_nothing(otel):
    app = _items_app(otel)
    _request(
        app,
        "GET",
        "/api/v1/items/1",
        headers={"traceparent": _traceparent(IN_RATIO_TRACE_ID)},
    )
    assert otel.spans() == []


def test_incoming_tracestate_and_baggage_are_discarded(otel):
    app = FastAPI()
    seen = {}

    @app.get("/api/v1/context")
    async def current():
        seen["baggage"] = dict(baggage.get_all())
        seen["trace_state"] = trace.get_current_span().get_span_context().trace_state
        return {}

    wrapped = otel.app(app)
    _request(
        wrapped,
        "GET",
        "/api/v1/context",
        headers={
            "traceparent": _traceparent(IN_RATIO_TRACE_ID),
            "tracestate": f"vendor={SECRET.replace('-', '_')}",
            "baggage": f"user.id={SECRET}",
        },
    )

    span = otel.spans()[0]
    assert format(span.context.trace_id, "032x") == IN_RATIO_TRACE_ID
    assert len(span.context.trace_state) == 0
    assert len(seen["trace_state"]) == 0
    assert seen["baggage"] == {}


def test_duplicate_traceparent_headers_do_not_break_requests(otel):
    app = _items_app(otel)

    response = _request(
        app,
        "GET",
        "/api/v1/items/1",
        headers=[
            ("traceparent", _traceparent(IN_RATIO_TRACE_ID)),
            ("traceparent", _traceparent(OUT_OF_RATIO_TRACE_ID)),
        ],
    )

    assert response.status_code == 200
    assert len(otel.spans()) == 1


def test_root_client_spans_are_not_recorded():
    sampler = tracing.build_sampler(1.0)

    client = sampler.should_sample(None, 1, "GET", SpanKind.CLIENT)
    server = sampler.should_sample(None, 1, "GET", SpanKind.SERVER)
    internal = sampler.should_sample(None, 1, "job", SpanKind.INTERNAL)

    assert client.decision == Decision.DROP
    assert server.decision == Decision.RECORD_AND_SAMPLE
    assert internal.decision == Decision.RECORD_AND_SAMPLE


########################################
# Client spans and outbound propagation
########################################


class _HeaderRecorder(BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append({k.lower(): v for k, v in self.headers.items()})
        if self.path.startswith("/redirect"):
            port = self.server.server_address[1]
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture
def header_server():
    _HeaderRecorder.seen = []
    server = HTTPServer(("127.0.0.1", 0), _HeaderRecorder)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1], _HeaderRecorder.seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _assert_propagation(otel, seen, port):
    clients = sorted(otel.spans(SpanKind.CLIENT), key=lambda span: span.start_time)
    assert len(clients) == 2
    allowed, external = seen
    version, trace_id, span_id, flags = allowed["traceparent"].split("-")
    assert (version, trace_id, span_id) == (
        "00",
        f"{clients[0].context.trace_id:032x}",
        f"{clients[0].context.span_id:016x}",
    )
    assert int(flags, 16) & 0x01
    assert "traceparent" not in external
    for headers in seen:
        assert "baggage" not in headers
        assert "tracestate" not in headers
    assert clients[0].attributes["url.full"] == f"http://localhost:{port}"
    assert clients[1].attributes["url.full"] == f"http://127.0.0.1:{port}"
    for span in clients:
        assert SECRET not in _all_attribute_values(span)
        assert span.parent is not None


@pytest.mark.parametrize("client", ["requests", "httpx", "httpx-async"])
def test_sync_clients_propagate_only_to_allowed_hosts(otel, header_server, client):
    port, seen = header_server
    otel.instrument_libraries()
    urls = [
        f"http://{host}:{port}/call?token={SECRET}"
        for host in (ALLOWED_HOST, "127.0.0.1")
    ]

    def call(url):
        if client == "requests":
            requests.get(url, timeout=5)
        elif client == "httpx":
            httpx.get(url, timeout=5)
        else:

            async def call_async():
                async with httpx.AsyncClient(timeout=5) as http:
                    await http.get(url)

            asyncio.run(call_async())

    token = context.attach(baggage.set_baggage("user.id", SECRET))
    try:
        with otel.tracer.start_as_current_span("request", kind=SpanKind.SERVER):
            for url in urls:
                call(url)
    finally:
        context.detach(token)

    _assert_propagation(otel, seen, port)


@pytest.mark.parametrize("client", ["requests", "httpx", "httpx-async", "aiohttp"])
def test_redirect_to_unapproved_host_drops_traceparent(otel, header_server, client):
    port, seen = header_server
    otel.instrument_libraries()
    url = f"http://{ALLOWED_HOST}:{port}/redirect"

    async def call_async():
        if client == "aiohttp":
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    await response.read()
        else:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as http:
                await http.get(url)

    with otel.tracer.start_as_current_span("request", kind=SpanKind.SERVER):
        if client == "requests":
            requests.get(url, timeout=5)
        elif client == "httpx":
            httpx.get(url, timeout=5, follow_redirects=True)
        else:
            asyncio.run(call_async())

    assert len(seen) == 2
    assert seen[0]["host"] == f"{ALLOWED_HOST}:{port}"
    assert "traceparent" in seen[0]
    assert seen[1]["host"] == f"127.0.0.1:{port}"
    assert "traceparent" not in seen[1]
    assert "tracestate" not in seen[1]


def test_orphan_client_calls_are_not_recorded(otel, header_server):
    port, seen = header_server
    otel.instrument_libraries()

    requests.get(f"http://{ALLOWED_HOST}:{port}/heartbeat", timeout=5)
    requests.get(f"http://127.0.0.1:{port}/heartbeat", timeout=5)

    assert otel.spans() == []
    # Internal hosts may still receive an unsampled context; others get none.
    assert not int(seen[0]["traceparent"].split("-")[3], 16) & 0x01
    assert "traceparent" not in seen[1]


def test_aiohttp_call_from_route_is_a_child_client_span(otel):
    otel.instrument_libraries()
    seen = []

    async def echo(request):
        seen.append({k.lower(): v for k, v in request.headers.items()})
        return web.Response(text="ok")

    app = FastAPI()

    async def run():
        server = web.Application()
        server.router.add_get("/echo", echo)
        runner = web.AppRunner(server)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]

        @app.get("/api/v1/proxy/{host}")
        async def proxy(host: str):
            token = context.attach(baggage.set_baggage("user.id", SECRET))
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://{host}:{port}/echo?token={SECRET}"
                    ) as response:
                        return {"status": response.status}
            finally:
                context.detach(token)

        wrapped = otel.app(app)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=wrapped), base_url="http://testserver"
            ) as client:
                for host in (ALLOWED_HOST, "127.0.0.1"):
                    response = await client.get(f"/api/v1/proxy/{host}")
                    assert response.json() == {"status": 200}
        finally:
            await runner.cleanup()
        return port

    port = asyncio.run(run())

    servers = sorted(otel.spans(SpanKind.SERVER), key=lambda span: span.start_time)
    clients = sorted(otel.spans(SpanKind.CLIENT), key=lambda span: span.start_time)
    assert [span.name for span in servers] == ["GET /api/v1/proxy/{host}"] * 2
    assert [span.name for span in clients] == ["GET"] * 2
    for server, client in zip(servers, clients):
        assert client.parent.span_id == server.context.span_id
        assert client.context.trace_id == server.context.trace_id
        assert client.attributes["http.request.method"] == "GET"
        assert client.attributes["http.response.status_code"] == 200
    _assert_propagation(otel, seen, port)


def test_sqlalchemy_query_from_route_never_records_parameter_values(otel):
    otel.instrument_libraries()
    # Engines are traced only if created through the patched module attribute.
    engine = sqlalchemy.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    app = FastAPI()

    @app.get("/api/v1/query")
    def query():
        with engine.connect() as connection:
            connection.execute(text("SELECT :value AS v"), {"value": SECRET}).all()
            connection.execute(text(f"SELECT '{SECRET}', 42 AS n")).all()
        return {}

    try:
        assert _request(otel.app(app), "GET", "/api/v1/query").status_code == 200
    finally:
        engine.dispose()

    server = otel.spans(SpanKind.SERVER)[0]
    queries = sorted(
        (span for span in otel.spans(SpanKind.CLIENT) if span.name != "connect"),
        key=lambda span: span.start_time,
    )
    assert len(queries) == 2
    assert [span.attributes["db.statement"] for span in queries] == [
        "SELECT ? AS v",
        "SELECT ?, ? AS n",
    ]
    for span in queries:
        assert span.parent.span_id == server.context.span_id
        assert span.attributes["db.system"] == "sqlite"
        assert SECRET not in _all_attribute_values(span)
        assert "db.user" not in span.attributes


class _RespHandler(socketserver.BaseRequestHandler):
    """Answers every RESP command with +OK, enough for redis-py SET/PING."""

    def handle(self):
        buffer = b""
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            buffer += data
            while True:
                consumed = self._command_length(buffer)
                if consumed is None:
                    break
                buffer = buffer[consumed:]
                self.request.sendall(b"+OK\r\n")

    @staticmethod
    def _command_length(buffer: bytes) -> int | None:
        end = buffer.find(b"\r\n")
        if end < 0:
            return None
        position = end + 2
        for _ in range(int(buffer[1:end])):
            end = buffer.find(b"\r\n", position)
            if end < 0:
                return None
            position = end + 2 + int(buffer[position + 1 : end]) + 2
            if position > len(buffer):
                return None
        return position


def test_redis_command_is_a_child_client_span_without_values(otel):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _RespHandler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    otel.instrument_libraries()
    client = redis.Redis(host="127.0.0.1", port=server.server_address[1])
    try:
        with otel.tracer.start_as_current_span("request", kind=SpanKind.SERVER):
            assert client.set("obs20:key", SECRET) is True
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    parent = otel.spans(SpanKind.SERVER)[0]
    commands = [span for span in otel.spans(SpanKind.CLIENT) if span.name == "SET"]
    assert len(commands) == 1
    span = commands[0]
    assert span.parent.span_id == parent.context.span_id
    assert span.attributes["db.statement"] == "SET ? ?"
    assert span.attributes["db.system"] == "redis"
    assert SECRET not in _all_attribute_values(span)


def test_libraries_are_instrumented_once(otel):
    otel.instrument_libraries()
    assert tracing.instrument_libraries(
        tracer_provider=otel.bundle.tracer_provider,
        meter_provider=otel.bundle.meter_provider,
    )


########################################
# Propagator and sanitizer units
########################################


def test_inject_requires_hook_approval_for_the_current_span(otel):
    propagator = tracing.TraceContextAllowListPropagator()
    with otel.tracer.start_as_current_span("request", kind=SpanKind.SERVER):
        with otel.tracer.start_as_current_span("call", kind=SpanKind.CLIENT) as span:
            carrier = {}
            propagator.inject(carrier)
            assert carrier == {}

            assert tracing.approve_propagation(span, "https://evil.example/") is False
            propagator.inject(carrier)
            assert carrier == {}

            assert tracing.approve_propagation(span, "http://LOCALHOST./x") is True
            propagator.inject(carrier)
            assert set(carrier) == {"traceparent"}

        with otel.tracer.start_as_current_span("next", kind=SpanKind.CLIENT):
            # Approval is bound to one client span, not to the task.
            carrier = {}
            propagator.inject(carrier)
            assert carrier == {}
    assert propagator.fields == {"traceparent", "tracestate"}


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("pipelines.internal", True),
        ("PIPELINES.INTERNAL.", True),
        ("a.svc.cluster.local", True),
        ("svc.cluster.local", False),
        ("evilsvc.cluster.local", False),
        ("pipelines.internal.evil.example", False),
        ("api.openai.com", False),
        ("", False),
        (None, False),
    ],
)
def test_host_allow_list_matching(host, allowed):
    hosts = ("pipelines.internal", "*.svc.cluster.local")
    assert tracing.host_allowed(host, hosts) is allowed


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            f"https://user:{SECRET}@api.example:8443/v1/x?key={SECRET}#f",
            "https://api.example:8443",
        ),
        ("http://[::1]:8080/path", "http://[::1]:8080"),
        ("http://127.0.0.1/path", "http://127.0.0.1"),
        ("/relative/path", None),
        ("http://bad:port/", None),
    ],
)
def test_url_origin(value, expected):
    assert tracing.url_origin(value) == expected


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("SELECT * FROM chat WHERE id = ?", "SELECT * FROM chat WHERE id = ?"),
        (
            "SELECT * FROM t WHERE a = 'x''y' AND b = 12.5e3",
            "SELECT * FROM t WHERE a = ? AND b = ?",
        ),
        (
            "SELECT col1, t2.c3 FROM t2 WHERE n = $1",
            "SELECT col1, t2.c3 FROM t2 WHERE n = $1",
        ),
        ("SELECT 'unterminated", "SELECT ?"),
    ],
)
def test_sanitize_sql(statement, expected):
    assert tracing.sanitize_sql(statement) == expected


def test_sanitize_sql_bounds_length():
    assert len(tracing.sanitize_sql("SELECT " + "x" * 10000)) == 2048


def test_sanitized_span_keeps_only_allow_listed_data():
    span_context = SpanContext(1, 2, False, TraceFlags(1))
    span = ReadableSpan(
        name="POST /api/v1/chats/{id}",
        context=span_context,
        attributes={
            "http.request.method": "POST",
            "http.route": "/api/v1/chats/{id}",
            "url.full": f"https://api.example/v1?key={SECRET}",
            "url.path": f"/api/v1/chats/{SECRET}",
            "http.request.header.authorization": (SECRET,),
            "db.user": SECRET,
            "db.statement": f"SELECT '{SECRET}'",
            "canchat.request.id": REQUEST_ID,
        },
        events=(
            Event(
                "exception",
                {
                    "exception.type": "ValueError",
                    "exception.message": SECRET,
                    "exception.stacktrace": SECRET,
                    "exception.escaped": "False",
                },
            ),
        ),
        links=(Link(span_context, {"http.route": "/x", "user.email": SECRET}),),
        kind=SpanKind.SERVER,
        status=Status(StatusCode.ERROR, f"ValueError: {SECRET}"),
        start_time=1,
        end_time=2,
    )

    sanitized = tracing.sanitize_span(span)

    assert dict(sanitized.attributes) == {
        "http.request.method": "POST",
        "http.route": "/api/v1/chats/{id}",
        "url.full": "https://api.example",
        "db.statement": "SELECT ?",
        "canchat.request.id": REQUEST_ID,
    }
    assert dict(sanitized.events[0].attributes) == {
        "exception.type": "ValueError",
        "exception.escaped": "False",
    }
    assert dict(sanitized.links[0].attributes) == {"http.route": "/x"}
    assert sanitized.status.status_code == StatusCode.ERROR
    assert sanitized.status.description is None
    assert sanitized.context == span_context
    assert (sanitized.kind, sanitized.start_time, sanitized.end_time) == (
        SpanKind.SERVER,
        1,
        2,
    )
