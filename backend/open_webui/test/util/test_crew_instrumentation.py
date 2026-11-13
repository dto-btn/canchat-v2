"""OBS-26: CrewAI path instrumentation with explicit context propagation.

The ``crew-mcp-query`` socket handler and ``CrewMCPManager`` run for real.
CrewAI's ``Agent``/``Task``/``Crew`` are stubbed, but the model is a real
CrewAI ``BaseLLM`` subclass and the MCP tool a real ``BaseTool`` subclass, so
the traced subclasses applied by ``trace_crew_llm``/``trace_crew_tools`` are
exercised as in production. Spans go through the production sanitizing
exporter into memory; metrics through ``MetricsService`` (strict).
"""

import asyncio
import gc
import importlib.util
import os
import sys
import threading
import types
import weakref
from pathlib import Path

import pytest

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from crewai.llms.base_llm import BaseLLM  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode  # noqa: E402

from open_webui.config import DEFAULT_METRIC_DEFINITIONS  # noqa: E402
from open_webui.metrics.service import MetricsService  # noqa: E402
from open_webui.models import message_metrics  # noqa: E402
from open_webui.observability import crew as crew_obs  # noqa: E402
from open_webui.observability import llm as llm_obs  # noqa: E402
from open_webui.observability import socketio as socketio_obs  # noqa: E402
from open_webui.observability.context import (  # noqa: E402
    current_crew_operation_var,
    operation_id_var,
    user_id_var,
)
from open_webui.observability.providers import create_providers  # noqa: E402
from open_webui.socket import main as socket_main  # noqa: E402
from open_webui.test.util.test_chat_instrumentation import otel_settings  # noqa: E402

DURATION = crew_obs.CREW_OPERATION_DURATION
MODEL = "azure/gpt-dep"
CHAT_ID = "6f1c2f9e-0b8a-4d6e-9a43-2f6b8f0c1d22"
CHAT_A = "0b8a6f1c-2f9e-4d6e-9a43-2f6b8f0c1d2a"
CHAT_B = "0b8a6f1c-2f9e-4d6e-9a43-2f6b8f0c1d2b"
USAGE = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}


class FakeAzureLLM(BaseLLM):
    """CrewAI model whose calls report usage like the native providers."""

    def call(self, messages, *args, **kwargs):
        self._track_token_usage_internal(
            {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
        )
        return "thought"


class TimeTool(BaseTool):
    name: str = "get_current_time"
    description: str = "Current time"

    def _run(self, **kwargs):
        return SCRIPT.tool(**kwargs)


class Script:
    """What the stubbed crew does; replaced per test."""

    def __init__(self):
        self.seen = []
        self.tool_result = "12:00"
        self.fail_llm = False
        self.before_kickoff = None
        self.usage = USAGE

    def tool(self, **kwargs):
        span = trace.get_current_span()
        self.seen.append(
            {
                "user_id": user_id_var.get(),
                "operation_id": operation_id_var.get(),
                "crew": current_crew_operation_var.get(),
                "trace_id": span.get_span_context().trace_id,
                "thread": threading.get_ident(),
            }
        )
        return self.tool_result


SCRIPT = Script()


class FakeCrewOutput:
    def __init__(self, text, usage):
        self.text = text
        self.token_usage = types.SimpleNamespace(**usage)

    def __str__(self):
        return self.text


class FakeCrew:
    def __init__(self, agents, tasks, **kwargs):
        self.agent = agents[0]
        self.task = tasks[0]

    def kickoff(self):
        if SCRIPT.before_kickoff:
            SCRIPT.before_kickoff()
        llm, tool = self.agent.llm, self.agent.tools[0]
        if SCRIPT.fail_llm:
            raise_from_llm(llm)
        llm.call([{"role": "user", "content": self.task.description}])
        observation = tool.run(timezone="UTC")
        llm.call([{"role": "user", "content": observation}])
        return FakeCrewOutput(f"It is {observation}", SCRIPT.usage)


class ProviderError(RuntimeError):
    pass


def raise_from_llm(llm):
    def failing_call(*args, **kwargs):
        raise ProviderError(f"provider rejected: {args}")

    # The traced class wraps ``call``: make the underlying provider fail.
    base = type(llm).__mro__[1]
    original = base.call
    base.call = failing_call
    try:
        llm.call("prompt")
    finally:
        base.call = original


class Telemetry:
    def __init__(self):
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.bundle = create_providers(
            otel_settings(),
            metric_readers=[self.reader],
            span_exporters=[self.exporter],
        )
        self.metrics = MetricsService(
            self.bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True
        )

    def spans(self, name=None):
        spans = self.exporter.get_finished_spans()
        return [s for s in spans if name is None or s.name == name]

    def span(self, name):
        (span,) = self.spans(name)
        return span

    def points(self, name=DURATION):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == name:
                        return list(metric.data.data_points)
        return []


def _tracer_from(provider, name):
    return lambda: provider.get_tracer(name)


@pytest.fixture
def telemetry(monkeypatch):
    t = Telemetry()
    provider = t.bundle.tracer_provider
    monkeypatch.setattr(
        crew_obs, "_tracer", _tracer_from(provider, crew_obs.TRACER_NAME)
    )
    monkeypatch.setattr(llm_obs, "_tracer", _tracer_from(provider, llm_obs.TRACER_NAME))
    monkeypatch.setattr(
        socketio_obs, "_tracer", _tracer_from(provider, socketio_obs.TRACER_NAME)
    )
    monkeypatch.setattr(crew_obs, "_default_metrics_service", lambda: t.metrics)
    yield t
    t.bundle.shutdown()


@pytest.fixture
def integration(monkeypatch):
    crewai = types.ModuleType("crewai")
    crewai.Agent = lambda **kwargs: types.SimpleNamespace(**kwargs)
    crewai.Task = lambda **kwargs: types.SimpleNamespace(**kwargs)
    crewai.Crew = FakeCrew
    crewai.Process = types.SimpleNamespace(sequential="sequential")
    # Like ``crewai.LLM``: ``azure/<deployment>`` selects the Azure provider.
    crewai.LLM = lambda model, **kwargs: FakeAzureLLM(
        model=model.split("/", 1)[1], provider="azure"
    )
    tools = types.ModuleType("crewai_tools")
    tools.MCPServerAdapter = None
    mcp = types.ModuleType("mcp")
    mcp.StdioServerParameters = lambda **kwargs: types.SimpleNamespace(**kwargs)
    source = (
        Path(__file__).resolve().parents[3]
        / "mcp_backend/integration/crew_mcp_integration.py"
    )
    spec = importlib.util.spec_from_file_location("obs26_crew_integration", source)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "crewai", crewai)
        imports.setitem(sys.modules, "crewai_tools", tools)
        imports.setitem(sys.modules, "mcp", mcp)
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_time_server_adapter", [TimeTool()])
    return module


@pytest.fixture
def manager(integration, tmp_path, monkeypatch):
    for name in ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"):
        monkeypatch.setenv(name, "unchanged")
    server = tmp_path / "time.py"
    server.touch()
    manager = integration.CrewMCPManager.__new__(integration.CrewMCPManager)
    manager.time_server_path = server
    manager.azure_config = types.SimpleNamespace(
        deployment="gpt-dep", api_key="", endpoint="", api_version=""
    )
    manager.get_available_tools = lambda: [{"name": "get_current_time"}]
    return manager


@pytest.fixture(autouse=True)
def script():
    global SCRIPT
    SCRIPT = Script()
    yield SCRIPT


class FakeSio:
    def __init__(self):
        self.emitted = []

    async def emit(self, event, data=None, room=None, **kwargs):
        self.emitted.append((event, data, room))

    def events(self, sid):
        return [event for event, _, room in self.emitted if room == sid]


@pytest.fixture
def socket(monkeypatch, manager):
    sio = FakeSio()
    sessions = {}
    stored = []
    monkeypatch.setattr(socket_main, "sio", sio)
    monkeypatch.setattr(socket_main, "SESSION_POOL", sessions)
    fake_module = types.ModuleType("mcp_backend.routers.crew_mcp")
    fake_module.crew_mcp_manager = manager
    monkeypatch.setitem(sys.modules, "mcp_backend.routers.crew_mcp", fake_module)
    monkeypatch.setattr(
        message_metrics.MessageMetrics,
        "insert_new_metrics",
        lambda **kwargs: stored.append(kwargs),
    )
    return types.SimpleNamespace(
        sio=sio, sessions=sessions, stored=stored, module=fake_module
    )


def _login(socket, sid, user_id, token="graph-token"):
    socket.sessions[sid] = {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "name": "Test User",
        "graph_access_token": token,
    }


def _payload(query="What time is it?", chat_id=CHAT_ID):
    return {
        "query": query,
        "model": "gpt-test",
        "selected_tools": ["mcp_time_server_get_current_time"],
        "chat_id": chat_id,
    }


def _query(sid, data):
    return socket_main.crew_mcp_query(sid, data)


def _children(telemetry, parent):
    return [
        s
        for s in telemetry.spans()
        if s.parent is not None and s.parent.span_id == parent.context.span_id
    ]


def test_span_tree_crosses_the_executor(telemetry, socket, integration):
    _login(socket, "sid-1", "user-1")
    caller_thread = threading.get_ident()

    asyncio.run(_query("sid-1", _payload()))

    assert socket.sio.events("sid-1") == ["crew-mcp-status", "crew-mcp-result"]
    operation = telemetry.span("crew.operation")
    run = telemetry.span("crew.run")
    kickoff = telemetry.span("crew.kickoff")
    tool = telemetry.span("crew.tool")
    llm_calls = telemetry.spans("llm.chat")
    attempts = telemetry.spans("llm.attempt")
    socket_event = telemetry.span("socketio.event")

    assert socket_event.parent is None
    assert operation.parent.span_id == socket_event.context.span_id
    assert run.parent.span_id == operation.context.span_id
    assert kickoff.parent.span_id == run.context.span_id
    assert tool.parent.span_id == kickoff.context.span_id
    assert len(llm_calls) == 2
    for llm_call in llm_calls:
        assert llm_call.parent.span_id == kickoff.context.span_id
    assert len(attempts) == 2
    assert {a.parent.span_id for a in attempts} == {
        c.context.span_id for c in llm_calls
    }
    assert {s.context.trace_id for s in telemetry.spans()} == {
        operation.context.trace_id
    }

    # The crew ran in another thread with the operation's context.
    (seen,) = SCRIPT.seen
    assert seen["thread"] != caller_thread
    assert seen["trace_id"] == operation.context.trace_id
    assert seen["user_id"] == "user-1"
    assert seen["crew"] is not None
    assert seen["operation_id"] == seen["crew"].operation_id

    assert operation.attributes["canchat.operation"] == "crew"
    assert operation.attributes["canchat.user.id"] == "user-1"
    assert operation.attributes["canchat.chat.id"] == CHAT_ID
    assert operation.attributes["canchat.crew.tools.selected"] == 1
    assert operation.attributes["canchat.model"] == MODEL
    assert operation.attributes["canchat.mcp.server"] == "time_server"
    assert operation.attributes["canchat.outcome"] == "success"
    assert operation.attributes["canchat.llm.usage.input_tokens"] == 20
    assert operation.attributes["canchat.llm.usage.output_tokens"] == 8
    assert run.attributes["canchat.mcp.server"] == "time_server"
    assert kickoff.attributes["canchat.crew.specialist"] == "time"
    assert tool.attributes["canchat.mcp.server"] == "time_server"
    assert tool.attributes["canchat.mcp.tool"] == "get_current_time"
    for llm_call in llm_calls:
        assert llm_call.attributes["canchat.model"] == MODEL
        assert llm_call.attributes["canchat.llm.provider"] == "azure"
        assert llm_call.attributes["canchat.chat.id"] == CHAT_ID
        assert llm_call.attributes["gen_ai.provider.name"] == "azure.ai.inference"
        assert llm_call.attributes["gen_ai.request.model"] == "gpt-dep"
        assert llm_call.attributes["canchat.llm.usage.input_tokens"] == 10
        assert llm_call.attributes["canchat.llm.usage.output_tokens"] == 4
        # Only the crew operation carries the actor.
        assert "canchat.user.id" not in llm_call.attributes
    assert all(s.status.status_code != StatusCode.ERROR for s in telemetry.spans())

    (point,) = telemetry.points()
    assert point.count == 1
    assert dict(point.attributes) == {"model": MODEL, "outcome": "success"}
    llm_requests = telemetry.points("canchat.llm.requests")
    assert sum(p.value for p in llm_requests) == 2
    # Token usage is stored once, from the crew result.
    assert len(socket.stored) == 1
    assert socket.stored[0]["usage"] == USAGE
    assert socket.stored[0]["model"] == MODEL


def test_shared_tools_are_not_modified(telemetry, socket, integration):
    _login(socket, "sid-1", "user-1")
    shared = integration._time_server_adapter[0]

    asyncio.run(_query("sid-1", _payload()))
    asyncio.run(_query("sid-1", _payload()))

    assert type(shared) is TimeTool
    assert integration._time_server_adapter == [shared]
    assert len(telemetry.spans("crew.tool")) == 2
    assert len(telemetry.spans("crew.operation")) == 2
    (point,) = telemetry.points()
    assert point.count == 2


def test_crew_error_finishes_once_with_model_usage(telemetry, socket):
    _login(socket, "sid-1", "user-1")
    SCRIPT.fail_llm = True

    asyncio.run(_query("sid-1", _payload()))

    assert socket.sio.events("sid-1") == ["crew-mcp-status", "crew-mcp-error"]
    operation = telemetry.span("crew.operation")
    assert operation.attributes["canchat.outcome"] == "error"
    assert operation.attributes["error.type"] == "ProviderError"
    assert operation.status.status_code == StatusCode.ERROR
    # Routing retried the time crew once (emergency fallback), both failed.
    assert len(telemetry.spans("crew.kickoff")) == 2
    for name in ("crew.run", "crew.kickoff", "llm.chat", "llm.attempt"):
        for span in telemetry.spans(name):
            assert span.attributes["error.type"] == "ProviderError", name
            assert span.status.status_code == StatusCode.ERROR, name
    for span in telemetry.spans("llm.chat"):
        assert span.attributes["canchat.llm.usage.known"] is False
    assert operation.attributes["canchat.llm.usage.known"] is False
    (point,) = telemetry.points()
    assert point.count == 1
    assert dict(point.attributes) == {
        "model": MODEL,
        "outcome": "error",
        "error.type": "ProviderError",
    }
    assert socket.stored == []


def test_crew_usage_falls_back_to_model_calls(telemetry, socket):
    _login(socket, "sid-1", "user-1")
    SCRIPT.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    asyncio.run(_query("sid-1", _payload()))

    operation = telemetry.span("crew.operation")
    assert operation.attributes["canchat.llm.usage.known"] is True
    assert operation.attributes["canchat.llm.usage.input_tokens"] == 20
    assert operation.attributes["canchat.llm.usage.output_tokens"] == 8
    assert socket.stored == []


@pytest.mark.parametrize(
    "setup, data, error_type",
    [
        (lambda s: None, _payload(query=""), "invalid_request"),
        (
            lambda s: setattr(s.module, "crew_mcp_manager", None),
            _payload(),
            "unavailable",
        ),
        (
            lambda s: setattr(
                s.module.crew_mcp_manager, "get_available_tools", lambda: []
            ),
            _payload(),
            "unavailable",
        ),
    ],
)
def test_rejected_query_finishes_once(telemetry, socket, setup, data, error_type):
    _login(socket, "sid-1", "user-1")
    setup(socket)

    asyncio.run(_query("sid-1", data))

    assert socket.sio.events("sid-1")[-1] == "crew-mcp-error"
    operation = telemetry.span("crew.operation")
    assert operation.attributes["canchat.outcome"] == "error"
    assert operation.attributes["error.type"] == error_type
    assert telemetry.spans("crew.run") == []
    (point,) = telemetry.points()
    assert point.count == 1
    assert point.attributes["error.type"] == error_type


def test_unauthenticated_query_has_no_operation(telemetry, socket):
    asyncio.run(_query("unknown-sid", _payload()))

    assert socket.sio.events("unknown-sid") == ["crew-mcp-error"]
    # Only the rejected socket event is traced; no crew operation starts.
    (event,) = telemetry.spans()
    assert event.name == "socketio.event"
    assert event.attributes["canchat.outcome"] == "rejected"
    assert telemetry.points() == []


def test_cancellation_finishes_once(telemetry, socket):
    _login(socket, "sid-1", "user-1")
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        release.wait(timeout=10)

    SCRIPT.before_kickoff = block

    async def run():
        task = asyncio.create_task(_query("sid-1", _payload()))
        await asyncio.to_thread(started.wait, 10)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    operation = telemetry.span("crew.operation")
    assert operation.attributes["canchat.outcome"] == "cancelled"
    assert operation.status.status_code != StatusCode.ERROR
    assert "error.type" not in operation.attributes
    (point,) = telemetry.points()
    assert point.count == 1
    assert dict(point.attributes) == {"model": MODEL, "outcome": "cancelled"}
    # The crew thread still finished under the cancelled operation's trace.
    assert telemetry.span("crew.run").context.trace_id == operation.context.trace_id
    assert socket.stored == []


def test_concurrent_crews_keep_separate_trace_and_user(telemetry, socket):
    _login(socket, "sid-a", "user-a")
    _login(socket, "sid-b", "user-b")
    barrier = threading.Barrier(2)
    # Both crews are inside their worker threads at the same time.
    SCRIPT.before_kickoff = lambda: barrier.wait(timeout=10)

    async def run():
        await asyncio.gather(
            _query("sid-a", _payload(chat_id=CHAT_A)),
            _query("sid-b", _payload(chat_id=CHAT_B)),
        )

    asyncio.run(run())

    operations = {
        s.attributes["canchat.user.id"]: s for s in telemetry.spans("crew.operation")
    }
    assert set(operations) == {"user-a", "user-b"}
    trace_a = operations["user-a"].context.trace_id
    trace_b = operations["user-b"].context.trace_id
    assert trace_a != trace_b
    assert operations["user-a"].attributes["canchat.chat.id"] == CHAT_A
    assert operations["user-b"].attributes["canchat.chat.id"] == CHAT_B

    seen = {entry["user_id"]: entry for entry in SCRIPT.seen}
    assert set(seen) == {"user-a", "user-b"}
    assert seen["user-a"]["trace_id"] == trace_a
    assert seen["user-b"]["trace_id"] == trace_b
    assert seen["user-a"]["crew"] is not seen["user-b"]["crew"]

    for span in telemetry.spans():
        if span.name == "crew.operation":
            continue
        assert span.context.trace_id in {trace_a, trace_b}
    for name in ("crew.run", "crew.kickoff", "crew.tool"):
        assert {s.context.trace_id for s in telemetry.spans(name)} == {
            trace_a,
            trace_b,
        }
    for llm_call in telemetry.spans("llm.chat"):
        expected = CHAT_A if llm_call.context.trace_id == trace_a else CHAT_B
        assert llm_call.attributes["canchat.chat.id"] == expected
    (point,) = telemetry.points()
    assert point.count == 2


def test_finish_is_idempotent_and_reported_failure_is_an_error(telemetry):
    operation = crew_obs.CrewOperation.start(
        user_id="user-1", chat_id=CHAT_ID, metrics_service=telemetry.metrics
    )
    with operation.activate():
        with crew_obs.crew_span("crew.kickoff"):
            # A SharePoint failure turned into a user-facing apology.
            crew_obs.mark_crew_failed(PermissionError("denied for user@example.com"))
    operation.finish()
    operation.finish("cancelled")

    span = telemetry.span("crew.operation")
    assert span.attributes["canchat.outcome"] == "error"
    assert span.attributes["error.type"] == "PermissionError"
    assert telemetry.span("crew.kickoff").attributes["error.type"] == "PermissionError"
    (point,) = telemetry.points()
    assert point.count == 1
    assert current_crew_operation_var.get() is None


def test_operation_rejects_unbounded_identifiers(telemetry):
    operation = crew_obs.CrewOperation.start(
        user_id="someone@example.com",
        chat_id=CHAT_ID,
        metrics_service=telemetry.metrics,
    )
    operation.set_model("azure/a model with spaces")
    operation.set_server("server name")
    with operation.activate():
        pass

    span = telemetry.span("crew.operation")
    assert "canchat.user.id" not in span.attributes
    assert "canchat.model" not in span.attributes
    assert "canchat.mcp.server" not in span.attributes
    (point,) = telemetry.points()
    assert point.attributes["model"] == "unknown"


def test_per_connection_tool_classes_are_not_kept_alive(telemetry):
    """MCP adapters define a new tool class per connection (SharePoint runs)."""
    refs = []
    for _ in range(20):

        class ConnectionTool(BaseTool):
            name: str = "search_documents"
            description: str = "Search"

            def _run(self, **kwargs):
                return "ok"

        (traced,) = crew_obs.trace_crew_tools([ConnectionTool()], "mpo_server")
        assert traced.run() == "ok"
        assert crew_obs.trace_crew_tool(traced) is traced
        refs.append(weakref.ref(ConnectionTool))
        del ConnectionTool, traced
    gc.collect()

    assert sum(ref() is not None for ref in refs) <= 1
    assert len(telemetry.spans("crew.tool")) == 20


def test_traced_llm_class_is_reused():
    first = crew_obs.trace_crew_llm(FakeAzureLLM(model="a", provider="azure"))
    second = crew_obs.trace_crew_llm(FakeAzureLLM(model="b", provider="azure"))
    assert type(first) is type(second)
    assert type(first).__mro__[1] is FakeAzureLLM
    assert crew_obs.trace_crew_llm(first) is first
    assert type(first).__mro__[1] is FakeAzureLLM


def test_non_crewai_objects_are_returned_unchanged():
    tool = types.SimpleNamespace(name="t")
    assert crew_obs.trace_crew_tools([tool, None]) == [tool, None]
    assert crew_obs.trace_crew_llm(None) is None


def test_crew_path_keeps_canaries_out_of_every_sink(
    canary_harness, monkeypatch, socket
):
    """Canary run (OBS-17) over the crew path, on the global tracer provider."""
    canaries = canary_harness.canaries
    reader = InMemoryMetricReader()
    bundle = create_providers(otel_settings(), metric_readers=[reader])
    metrics = MetricsService(bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True)
    monkeypatch.setattr(crew_obs, "_default_metrics_service", lambda: metrics)
    socket.sessions["sid-1"] = {
        "id": "user-1",
        "email": canaries.email,
        "name": canaries.name,
        "graph_access_token": canaries.token,
    }
    SCRIPT.tool_result = canaries.prompt

    try:
        asyncio.run(_query("sid-1", _payload(query=canaries.prompt)))
        SCRIPT.fail_llm = True
        asyncio.run(_query("sid-1", _payload(query=canaries.prompt)))
    finally:
        bundle.shutdown()

    spans = canary_harness.sinks()["spans"]
    for name in ["crew.operation", "crew.run", "crew.kickoff", "crew.tool", "llm.chat"]:
        assert f'"name": "{name}"' in spans, name
    # The handler sends the result (and error text) to the user's own socket.
    canary_harness.assert_no_canaries()
