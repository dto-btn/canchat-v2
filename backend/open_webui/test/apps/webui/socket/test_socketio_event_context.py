"""OBS-27: Socket.IO per-event context and browser operation carrier.

Handlers run for real through ``observe_event``; ``sio`` and the CrewAI
manager are stubbed. Spans go through the production sanitizing exporter
into memory and counters through a strict ``MetricsService``.
"""

import asyncio
import sys
import types

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from open_webui.config import DEFAULT_METRIC_DEFINITIONS  # noqa: E402
from open_webui.metrics import service as metrics_service  # noqa: E402
from open_webui.metrics.service import MetricsService  # noqa: E402
from open_webui.observability import socketio as socketio_obs  # noqa: E402
from open_webui.observability.context import (  # noqa: E402
    operation_id_var,
    request_id_var,
    user_id_var,
)
from open_webui.observability.providers import (  # noqa: E402
    OTelSettings,
    create_providers,
)
from open_webui.socket import main as socket_main  # noqa: E402

SID = "sid-obs27-0001"
USER_ID = "user-obs27"
OPERATION_ID = "op-0123456789abcdef"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_SPAN_ID}-01"


class FakeSio:
    def __init__(self):
        self.emitted = []

    async def emit(self, event, data=None, room=None, **kwargs):
        self.emitted.append((event, data, room))

    async def enter_room(self, sid, room):
        pass


class FakeCrewManager:
    def __init__(self, raise_error=False):
        self.raise_error = raise_error
        self.azure_config = types.SimpleNamespace(deployment=None)
        self.seen = []

    def get_available_tools(self):
        return [{"name": "fake_tool"}]

    def run_intelligent_crew(self, query, selected_tools, user_access_token):
        if self.raise_error:
            raise RuntimeError("boom")
        return ("answer", {}, None)


class Telemetry:
    def __init__(self):
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.bundle = create_providers(
            OTelSettings(
                enabled=False,
                service_name="test",
                service_version="test",
                environment="test",
                endpoint="http://collector:4318",
                meter_name="test",
                metric_export_interval_ms=15000,
                traces_sampler_ratio=1.0,
            ),
            metric_readers=[self.reader],
            span_exporters=[self.exporter],
        )
        self.metrics = MetricsService(
            self.bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True
        )
        self.metrics.register_callback(
            socketio_obs.SOCKETIO_CONNECTIONS, socketio_obs.connection_observations
        )

    def spans(self):
        return list(self.exporter.get_finished_spans())

    def points(self, name):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        return list(metric.data.data_points)
        return []

    def event_counts(self):
        return {
            (p.attributes["event"], p.attributes["outcome"]): p.value
            for p in self.points(socketio_obs.SOCKETIO_EVENTS)
        }

    def connections(self):
        points = self.points(socketio_obs.SOCKETIO_CONNECTIONS)
        return points[0].value if points else 0


@pytest.fixture
def telemetry(monkeypatch):
    t = Telemetry()
    monkeypatch.setattr(
        socketio_obs,
        "_tracer",
        lambda: t.bundle.tracer_provider.get_tracer(socketio_obs.TRACER_NAME),
    )
    monkeypatch.setattr(metrics_service, "_current_service", t.metrics)
    monkeypatch.setattr(socketio_obs, "_connections", {})
    yield t
    t.bundle.shutdown()


@pytest.fixture
def sio(monkeypatch):
    fake = FakeSio()
    monkeypatch.setattr(socket_main, "sio", fake)
    monkeypatch.setattr(
        socket_main, "SESSION_POOL", {SID: {"id": USER_ID, "name": "n"}}
    )
    monkeypatch.setattr(socket_main, "USER_POOL", {USER_ID: [SID]})
    return fake


@pytest.fixture
def crew(monkeypatch):
    def install(manager):
        module = types.ModuleType("mcp_backend.routers.crew_mcp")
        module.crew_mcp_manager = manager
        monkeypatch.setitem(sys.modules, "mcp_backend.routers.crew_mcp", module)
        return manager

    return install


def query(carrier=None):
    data = {"query": "hi", "model": "m", "selected_tools": ["fake_tool"]}
    if carrier is not None:
        data["carrier"] = carrier
    return data


def replies(fake_sio):
    return [
        (event, data)
        for event, data, _ in fake_sio.emitted
        if event.startswith("crew-mcp-")
    ]


def assert_context_clear():
    assert request_id_var.get() is None
    assert operation_id_var.get() is None
    assert user_id_var.get() is None
    assert socketio_obs.current_socket_event() is None
    assert not trace.get_current_span().get_span_context().is_valid


########################################
# Carrier validation
########################################


def test_valid_carrier_parents_span_and_is_echoed_on_replies(telemetry, sio, crew):
    crew(FakeCrewManager())
    socketio_obs.register_connection(SID, USER_ID)

    asyncio.run(
        socket_main.crew_mcp_query(
            SID, query({"operation_id": OPERATION_ID, "traceparent": TRACEPARENT})
        )
    )

    [span] = telemetry.spans()
    assert span.name == socketio_obs.SPAN_NAME
    assert span.kind is trace.SpanKind.SERVER
    assert format(span.context.trace_id, "032x") == TRACE_ID
    assert format(span.parent.span_id, "016x") == PARENT_SPAN_ID
    assert span.parent.is_remote
    assert span.attributes["canchat.request.id"] == OPERATION_ID
    assert span.attributes["canchat.socketio.event"] == "crew-mcp-query"
    assert span.attributes["canchat.outcome"] == "success"
    events = replies(sio)
    assert [e for e, _ in events] == ["crew-mcp-status", "crew-mcp-result"]
    assert all(data["operation_id"] == OPERATION_ID for _, data in events)


@pytest.mark.parametrize(
    "operation_id",
    [
        "short",
        "x" * 65,
        "has space 123",
        "semi;colon-1234",
        "unicode-é-12345",
        12345678901,
        None,
        ["op-0123456789abcdef"],
        SID,
    ],
    ids=[
        "too-short",
        "too-long",
        "space",
        "bad-char",
        "non-ascii",
        "number",
        "null",
        "list",
        "socket-id",
    ],
)
def test_invalid_operation_id_is_replaced(telemetry, sio, crew, operation_id):
    crew(FakeCrewManager())

    asyncio.run(
        socket_main.crew_mcp_query(
            SID, query({"operation_id": operation_id, "traceparent": TRACEPARENT})
        )
    )

    [span] = telemetry.spans()
    generated = span.attributes["canchat.request.id"]
    assert generated != operation_id
    assert generated != SID
    assert socketio_obs.REQUEST_ID_PATTERN.fullmatch(generated)
    assert all(data["operation_id"] == generated for _, data in replies(sio))


@pytest.mark.parametrize(
    "traceparent",
    [
        "garbage",
        f"00-{'0' * 32}-{PARENT_SPAN_ID}-01",
        f"00-{TRACE_ID}-{'0' * 16}-01",
        f"ff-{TRACE_ID}-{PARENT_SPAN_ID}-01",
        f"00-{TRACE_ID.upper()}-{PARENT_SPAN_ID}-01",
        TRACEPARENT + "-" + "a" * 300,
        12345,
        None,
    ],
)
def test_invalid_traceparent_starts_a_new_trace(telemetry, sio, crew, traceparent):
    crew(FakeCrewManager())

    asyncio.run(
        socket_main.crew_mcp_query(
            SID, query({"operation_id": OPERATION_ID, "traceparent": traceparent})
        )
    )

    [span] = telemetry.spans()
    assert span.parent is None
    assert format(span.context.trace_id, "032x") != TRACE_ID
    assert span.attributes["canchat.request.id"] == OPERATION_ID


@pytest.mark.parametrize("carrier", [None, "not-a-dict", ["x"], {}])
def test_missing_carrier_gets_server_operation_id(telemetry, sio, crew, carrier):
    crew(FakeCrewManager())
    data = query()
    if carrier is not None:
        data["carrier"] = carrier

    asyncio.run(socket_main.crew_mcp_query(SID, data))

    [span] = telemetry.spans()
    assert span.parent is None
    operation_id = span.attributes["canchat.request.id"]
    assert socketio_obs.REQUEST_ID_PATTERN.fullmatch(operation_id)
    assert operation_id != SID
    assert all(d["operation_id"] == operation_id for _, d in replies(sio))


def test_tracestate_and_baggage_in_carrier_are_ignored():
    carrier = socketio_obs.parse_carrier(
        {
            "carrier": {
                "operation_id": OPERATION_ID,
                "traceparent": TRACEPARENT,
                "tracestate": "vendor=secret",
                "baggage": "user=someone",
            }
        }
    )
    span_context = trace.get_current_span(carrier.parent).get_span_context()
    assert format(span_context.trace_id, "032x") == TRACE_ID
    assert len(span_context.trace_state) == 0


########################################
# Context lifecycle
########################################


def test_context_is_bound_during_event_and_reset_after(telemetry, sio):
    socketio_obs.register_connection(SID, USER_ID)
    seen = []

    @socketio_obs.observe_event("probe", span=True)
    async def probe(sid, data):
        seen.append(
            (
                request_id_var.get(),
                operation_id_var.get(),
                user_id_var.get(),
                trace.get_current_span().get_span_context().trace_id,
            )
        )

    async def run_two_events():
        await probe(
            SID, {"carrier": {"operation_id": OPERATION_ID, "traceparent": TRACEPARENT}}
        )
        assert_context_clear()
        await probe(SID, {})
        assert_context_clear()

    asyncio.run(run_two_events())

    first, second = seen
    assert first[:3] == (OPERATION_ID, OPERATION_ID, USER_ID)
    assert format(first[3], "032x") == TRACE_ID
    # Nothing from the first event leaks into the second on the same socket.
    assert second[0] not in (OPERATION_ID, None)
    assert second[2] == USER_ID
    assert format(second[3], "032x") != TRACE_ID


def test_context_is_reset_when_handler_raises(telemetry):
    @socketio_obs.observe_event("probe", span=True)
    async def probe(sid, data):
        raise ValueError("nope")

    async def run():
        with pytest.raises(ValueError):
            await probe(SID, {"carrier": {"operation_id": OPERATION_ID}})
        assert_context_clear()

    asyncio.run(run())
    [span] = telemetry.spans()
    assert span.attributes["canchat.outcome"] == "error"
    assert span.attributes["error.type"] == "ValueError"
    assert telemetry.event_counts() == {("probe", "error"): 1}


def test_ambient_context_does_not_parent_the_event(telemetry):
    """A span or IDs left in the receiving task must not leak into an event."""

    @socketio_obs.observe_event("probe", span=True)
    async def probe(sid, data):
        return request_id_var.get()

    async def run():
        tracer = telemetry.bundle.tracer_provider.get_tracer("ambient")
        token = request_id_var.set("ambient-request-id")
        try:
            with tracer.start_as_current_span("ambient") as ambient:
                result = await probe(SID, {})
                return ambient, result
        finally:
            request_id_var.reset(token)

    ambient, result = asyncio.run(run())
    assert result != "ambient-request-id"
    event_span = next(s for s in telemetry.spans() if s.name == socketio_obs.SPAN_NAME)
    assert event_span.parent is None
    assert event_span.context.trace_id != ambient.get_span_context().trace_id


def test_identity_comes_from_server_session_not_payload(telemetry):
    socketio_obs.register_connection(SID, USER_ID)

    @socketio_obs.observe_event("probe")
    async def probe(sid, data):
        return user_id_var.get()

    result = asyncio.run(
        probe(SID, {"user_id": "attacker", "carrier": {"user_id": "attacker"}})
    )
    assert result == USER_ID


def test_non_business_events_get_context_but_no_span(telemetry, sio):
    asyncio.run(socket_main.usage(SID, {"model": "m"}))
    asyncio.run(socket_main.user_list(SID))

    assert telemetry.spans() == []
    assert telemetry.event_counts() == {
        ("usage", "success"): 1,
        ("user-list", "success"): 1,
    }


########################################
# Connections, reconnects and outcomes
########################################


def test_connection_gauge_tracks_connect_disconnect_and_reconnect(
    telemetry, sio, monkeypatch
):
    user = types.SimpleNamespace(
        id=USER_ID, name="n", model_dump=lambda: {"id": USER_ID}
    )
    monkeypatch.setattr(socket_main, "decode_token", lambda token: {"id": USER_ID})
    monkeypatch.setattr(socket_main.Users, "get_user_by_id", lambda _id: user)

    asyncio.run(socket_main.connect("sid-a", {}, {"token": "t"}))
    assert telemetry.connections() == 1
    assert socketio_obs.connection_user_id("sid-a") == USER_ID

    # Reconnect: new socket ID before the old one is reported gone.
    asyncio.run(socket_main.connect("sid-b", {}, None))
    assert telemetry.connections() == 2
    assert socketio_obs.connection_user_id("sid-b") is None
    monkeypatch.setattr(socket_main.Channels, "get_channels_by_user_id", lambda _id: [])
    asyncio.run(socket_main.user_join("sid-b", {"auth": {"token": "t"}}))
    assert socketio_obs.connection_user_id("sid-b") == USER_ID

    asyncio.run(socket_main.disconnect("sid-a"))
    asyncio.run(socket_main.disconnect("sid-a"))
    assert telemetry.connections() == 1
    asyncio.run(socket_main.disconnect("sid-b"))
    assert telemetry.connections() == 0

    assert telemetry.event_counts() == {
        ("connect", "success"): 1,
        ("connect", "rejected"): 1,
        ("user-join", "success"): 1,
        ("disconnect", "success"): 3,
    }
    assert_context_clear()


def test_signature_retry_is_not_counted_twice(telemetry, sio):
    # python-socketio calls ``disconnect(sid, reason)`` first and retries with
    # ``disconnect(sid)`` on TypeError; only the successful call counts.
    with pytest.raises(TypeError):
        asyncio.run(socket_main.disconnect(SID, "client disconnect"))
    asyncio.run(socket_main.disconnect(SID))

    assert telemetry.event_counts() == {("disconnect", "success"): 1}


@pytest.mark.parametrize(
    "setup, data, outcome, code",
    [
        ("no-session", query(), "rejected", 401),
        ("ok", {"query": ""}, "rejected", 400),
        ("raise", query(), "error", 500),
    ],
)
def test_crew_outcomes_are_counted(
    telemetry, sio, crew, monkeypatch, setup, data, outcome, code
):
    crew(FakeCrewManager(raise_error=setup == "raise"))
    if setup == "no-session":
        monkeypatch.setattr(socket_main, "SESSION_POOL", {})

    asyncio.run(socket_main.crew_mcp_query(SID, data))

    [(event, payload)] = [r for r in replies(sio) if r[0] == "crew-mcp-error"]
    assert payload["code"] == code
    assert "operation_id" in payload
    assert telemetry.event_counts() == {("crew-mcp-query", outcome): 1}
    [span] = telemetry.spans()
    assert span.attributes["canchat.outcome"] == outcome
    assert (span.status.status_code is trace.StatusCode.ERROR) == (outcome == "error")


def test_event_label_is_bounded_to_registered_handlers(telemetry, sio):
    asyncio.run(socket_main.user_list(SID))
    asyncio.run(socket_main.usage(SID, {"model": "m", "event": "injected"}))
    for point in telemetry.points(socketio_obs.SOCKETIO_EVENTS):
        assert set(point.attributes) == {"event", "outcome"}
        assert point.attributes["event"] in {"user-list", "usage"}
