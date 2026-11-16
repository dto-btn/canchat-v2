"""OBS-29: MCP calls and subprocess stderr use the observability boundary."""

import asyncio
import importlib.util
import inspect
import json
import logging
import sys
import threading
from datetime import datetime, timedelta
from itertools import islice, product
from pathlib import Path
from string import ascii_lowercase
from types import SimpleNamespace
import types

import httpx
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import PythonStdioTransport
from mcp import StdioServerParameters
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, TextContent
from opentelemetry import propagate
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from mcp_backend.integration.observed_adapter import MCPServerAdapter
from mcp_backend.management import mcp_manager
from mcp_backend.management.mcp_manager import FastMCPManager
from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.metrics.service import MetricsService
from open_webui.observability import mcp as mcp_obs
from open_webui.observability.providers import OTelSettings, create_providers

SERVER = Path(__file__).with_name("mcp_observability_server.py")
TIME_SERVER = SERVER.parents[3] / "mcp_backend/servers/fastmcp_time_server.py"


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
        self.raw_exporter = InMemorySpanExporter()
        self.bundle.tracer_provider.add_span_processor(
            SimpleSpanProcessor(self.raw_exporter)
        )
        self.metrics = MetricsService(
            self.bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True
        )

    def tool_spans(self):
        return [
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "mcp.tool.call"
        ]

    def points(self):
        data = self.reader.get_metrics_data()
        return [
            point
            for resource in (data.resource_metrics if data else ())
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == mcp_obs.TOOL_DURATION
            for point in metric.data.data_points
        ]


@pytest.fixture
def telemetry(monkeypatch):
    observed = Telemetry()
    monkeypatch.setattr(
        mcp_obs,
        "_tracer",
        lambda: observed.bundle.tracer_provider.get_tracer(mcp_obs.TRACER_NAME),
    )
    monkeypatch.setattr(mcp_obs, "get_metrics_service", lambda: observed.metrics)
    yield observed
    observed.bundle.shutdown()


def _payload(canaries):
    return " ".join(
        [
            canaries.prompt,
            canaries.token,
            canaries.api_key,
            canaries.email,
            canaries.name,
            canaries.password,
        ]
    )


def _stderr_summaries(canary_harness):
    return [
        record
        for record in canary_harness.records
        if record.name == mcp_obs.__name__
        and record.getMessage().startswith("MCP subprocess stderr: [message omitted] (")
    ]


def _assert_success_telemetry(telemetry, parent_span):
    (span,) = telemetry.tool_spans()
    assert span.parent.span_id == parent_span.get_span_context().span_id
    assert dict(span.attributes) == {
        "canchat.mcp.server": "time_server",
        "canchat.mcp.tool": "get_current_time",
        "canchat.outcome": "success",
    }
    assert span.events == ()
    assert span.status.status_code == StatusCode.UNSET
    (point,) = telemetry.points()
    assert dict(point.attributes) == {
        "mcp.server": "time_server",
        "mcp.tool": "get_current_time",
        "outcome": "success",
    }
    assert point.count == 1
    assert point.sum > 0
    (raw_span,) = [
        span
        for span in telemetry.raw_exporter.get_finished_spans()
        if span.name == "mcp.tool.call"
    ]
    assert dict(raw_span.attributes) == dict(span.attributes)
    assert not raw_span.events


def test_manager_filters_real_subprocess_stderr(canary_harness, telemetry):
    canaries = canary_harness.canaries

    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config(
            "time_server", [sys.executable, str(SERVER)], builtin=True
        )
        try:
            assert await manager.start_server("time_server")
            return await manager.call_tool(
                "time_server",
                "get_current_time",
                {"payload": _payload(canaries), "reply": canaries.response},
            )
        finally:
            await manager.cleanup()

    original_propagator = propagate.get_global_textmap()
    try:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        with telemetry.bundle.tracer.start_as_current_span("parent") as parent:
            result = asyncio.run(invoke())
    finally:
        propagate.set_global_textmap(original_propagator)

    content = json.loads(result.content[0].text)
    assert content == {
        "payload": _payload(canaries),
        "reply": canaries.response,
        "trace_context_present": False,
    }
    _assert_success_telemetry(telemetry, parent)
    assert _stderr_summaries(canary_harness)
    canary_harness.assert_no_canaries()


def test_stub_detects_fastmcp_implicit_trace_metadata(telemetry):
    async def invoke():
        stderr = mcp_obs.FilteredStderrPipe()
        transport = PythonStdioTransport(script_path=SERVER, log_file=stderr.stream)
        try:
            async with Client(transport) as client:
                return await client.call_tool(
                    "get_current_time", {"payload": "safe", "reply": "safe"}
                )
        finally:
            await transport.close()
            stderr.close()

    original_propagator = propagate.get_global_textmap()
    try:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        with telemetry.bundle.tracer.start_as_current_span("parent"):
            result = asyncio.run(invoke())
    finally:
        propagate.set_global_textmap(original_propagator)

    content = result.content[0]
    assert isinstance(content, TextContent)
    assert json.loads(content.text)["trace_context_present"] is True


def test_crew_tool_uses_filtered_stdio_and_preserves_parent(canary_harness, telemetry):
    canaries = canary_harness.canaries
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    original_propagator = propagate.get_global_textmap()
    try:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        with telemetry.bundle.tracer.start_as_current_span("parent") as parent:
            adapter = MCPServerAdapter(params, "time_server")
            try:
                (tool,) = adapter.__enter__()
                result = tool.run(payload=_payload(canaries), reply=canaries.response)
            finally:
                adapter.__exit__(None, None, None)
    finally:
        propagate.set_global_textmap(original_propagator)

    assert json.loads(result) == {
        "payload": _payload(canaries),
        "reply": canaries.response,
        "trace_context_present": False,
    }
    assert not adapter._thread.is_alive()
    assert not adapter._stderr._thread.is_alive()
    _assert_success_telemetry(telemetry, parent)
    assert _stderr_summaries(canary_harness)
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize("client", ["manager", "crew"])
def test_builtin_time_server_tool_contract_is_unchanged(client):
    async def invoke_manager():
        manager = FastMCPManager()
        manager.add_server_config(
            "time_server", [sys.executable, str(TIME_SERVER)], builtin=True
        )
        try:
            assert await manager.start_server("time_server")
            return await manager.call_tool(
                "time_server", "get_current_time", {"timezone": "UTC", "format": "iso"}
            )
        finally:
            await manager.cleanup()

    if client == "manager":
        result = asyncio.run(invoke_manager()).content[0].text
    else:
        params = StdioServerParameters(command=sys.executable, args=[str(TIME_SERVER)])
        with MCPServerAdapter(params, "time_server") as tools:
            result = tools[0].run(timezone="UTC", format="iso")
    assert datetime.fromisoformat(result).utcoffset() == timedelta(0)


def test_reconfiguring_stdio_server_replaces_transport_and_stderr_pipe():
    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config("external", [sys.executable, str(SERVER)])
        try:
            assert await manager.start_server("external")
            old_stderr = manager._stderr_pipes["external"]
            result = await manager.call_tool(
                "external", "get_current_time", {"payload": "old", "reply": "server"}
            )
            assert json.loads(result.content[0].text)["payload"] == "old"

            manager.add_server_config("external", [sys.executable, str(TIME_SERVER)])
            assert await manager.start_server("external")
            assert old_stderr.stream.closed
            assert not old_stderr._thread.is_alive()
            assert manager._stderr_pipes["external"] is not old_stderr
            result = await manager.call_tool(
                "external", "get_current_time", {"timezone": "UTC", "format": "iso"}
            )
            assert datetime.fromisoformat(
                result.content[0].text
            ).utcoffset() == timedelta(0)
        finally:
            await manager.cleanup()

    asyncio.run(invoke())


@pytest.mark.parametrize("client", ["manager", "crew"])
def test_in_band_tool_failure_is_recorded_without_content(
    canary_harness, telemetry, client
):
    canaries = canary_harness.canaries
    args = {"payload": _payload(canaries), "reply": canaries.response, "mode": "error"}

    async def invoke_manager():
        manager = FastMCPManager()
        manager.add_server_config(
            "time_server", [sys.executable, str(SERVER)], builtin=True
        )
        try:
            assert await manager.start_server("time_server")
            with pytest.raises(Exception):
                await manager.call_tool("time_server", "get_current_time", args)
        finally:
            await manager.cleanup()

    if client == "manager":
        asyncio.run(invoke_manager())
    else:
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
        with MCPServerAdapter(params, "time_server") as tools:
            result = tools[0].run(**args)
            assert canaries.prompt in result

    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] == "error"
    assert span.status.status_code == StatusCode.ERROR
    assert not span.events
    assert canaries.prompt not in span.to_json()
    assert canaries.response not in span.to_json()
    (raw_span,) = [
        item
        for item in telemetry.raw_exporter.get_finished_spans()
        if item.name == "mcp.tool.call"
    ]
    assert canaries.prompt not in raw_span.to_json()
    assert canaries.response not in raw_span.to_json()
    assert not raw_span.events
    (point,) = telemetry.points()
    assert point.count == 1
    assert point.attributes["outcome"] == "error"
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (None, "success"),
        (ValueError("private input"), "error"),
        (TimeoutError("private input"), "timeout"),
        (httpx.ReadTimeout("private input"), "timeout"),
        (
            McpError(
                ErrorData(code=httpx.codes.REQUEST_TIMEOUT, message="private input")
            ),
            "timeout",
        ),
        (McpError(ErrorData(code=-32603, message="private input")), "error"),
        (asyncio.CancelledError("private input"), "cancelled"),
        ("in-band", "error"),
    ],
)
def test_manager_records_one_bounded_measurement_per_outcome(
    monkeypatch, telemetry, failure, outcome
):
    timeouts = []

    class Client:
        def __init__(self, _transport):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def call_tool(self, _tool, _arguments, *, timeout=None):
            timeouts.append(timeout)
            if isinstance(failure, BaseException):
                raise failure
            return SimpleNamespace(is_error=failure == "in-band")

    monkeypatch.setattr(mcp_manager, "ObservedMCPClient", Client)
    monkeypatch.setattr(mcp_manager, "MCP_TOOL_CALL_TIMEOUT", 7.5)
    clock = iter((10.0, 12.5))
    monkeypatch.setattr(mcp_obs, "_monotonic", lambda: next(clock))
    manager = FastMCPManager()
    manager.server_configs["user-chosen-server"] = {
        "transport": "http",
        "url": "https://example.invalid/mcp",
    }

    async def invoke():
        return await manager.call_tool(
            "user-chosen-server", "untrusted-tool-name", {"secret": "private input"}
        )

    if isinstance(failure, BaseException):
        with pytest.raises(type(failure)):
            asyncio.run(invoke())
    else:
        result = asyncio.run(invoke())
        assert result.is_error == (failure == "in-band")

    assert timeouts == [7.5]
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.mcp.server"] == "external"
    assert span.attributes["canchat.mcp.tool"] == "other"
    assert span.attributes["canchat.outcome"] == outcome
    assert span.status.status_code == (
        StatusCode.ERROR if outcome in {"error", "timeout"} else StatusCode.UNSET
    )
    assert not span.events
    assert "private input" not in span.to_json()
    (point,) = telemetry.points()
    assert dict(point.attributes) == {
        "mcp.server": "external",
        "mcp.tool": "other",
        "outcome": outcome,
    }
    assert point.count == 1
    assert point.sum == 2.5


@pytest.mark.parametrize("tool_fails", [False, True])
def test_metric_failure_does_not_change_tool_outcome(
    monkeypatch, telemetry, canary_harness, tool_fails
):
    class BrokenMetrics:
        strict = False

        def has_instrument(self, name):
            return True

        def record(self, name, value, attributes):
            raise RuntimeError(canary_harness.canaries.token)

    monkeypatch.setattr(mcp_obs, "get_metrics_service", BrokenMetrics)
    if tool_fails:
        with pytest.raises(ValueError, match="tool failed"):
            with mcp_obs.observe_tool("time_server", "get_current_time"):
                raise ValueError("tool failed")
    else:
        with mcp_obs.observe_tool("time_server", "get_current_time"):
            pass

    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] == ("error" if tool_fails else "success")
    assert any(
        record.getMessage() == "Failed to record MCP tool duration: RuntimeError"
        for record in canary_harness.records
    )
    canary_harness.assert_no_canaries()


def test_builtin_labels_are_trusted_bounded_and_only_use_known_tools(monkeypatch):
    monkeypatch.setattr(mcp_obs, "_builtin_servers", {"time_server", "news_server"})
    monkeypatch.setattr(mcp_obs, "MAX_BUILTIN_SERVERS", 3)

    assert mcp_obs.register_builtin_server("mpo_sharepoint_server") == (
        "mpo_sharepoint_server"
    )
    assert mcp_obs.tool_labels(
        "mpo_sharepoint_server", "mpo_search_documents_fast"
    ) == ("mpo_sharepoint_server", "mpo_search_documents_fast")
    assert mcp_obs.tool_labels("mpo_sharepoint_server", "private-tool") == (
        "mpo_sharepoint_server",
        "other",
    )
    assert mcp_obs.register_builtin_server("pmo_sharepoint_server") == "other"
    assert mcp_obs.register_builtin_server("private name_sharepoint_server") == "other"
    assert mcp_obs.tool_labels("unregistered", "get_current_time") == (
        "external",
        "other",
    )

    manager = FastMCPManager()
    manager.add_server_config("time_server", ["python", "server.py"])
    assert manager.server_configs["time_server"]["_observability_server"] == "external"


def test_builtin_server_label_cap_is_exactly_one_hundred(monkeypatch):
    monkeypatch.setattr(mcp_obs, "_builtin_servers", {"time_server", "news_server"})
    assert mcp_obs.MAX_BUILTIN_SERVERS == 100
    names = (
        f"{''.join(prefix)}_sharepoint_server"
        for prefix in product(ascii_lowercase, repeat=2)
    )
    for name in islice(names, 98):
        assert mcp_obs.register_builtin_server(name) == name
    assert len(mcp_obs._builtin_servers) == 100
    assert mcp_obs.register_builtin_server(next(names)) == "other"


def test_http_subprocess_stderr_is_filtered(canary_harness):
    canaries = canary_harness.canaries

    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config(
            "external",
            [sys.executable, str(SERVER), "--http"],
            env={"OBS29_TEST_STDERR": _payload(canaries)},
            transport="http",
        )
        try:
            assert await manager.start_server("external")
            process = manager.server_processes["external"]
            await asyncio.to_thread(process.wait, 10)
        finally:
            await manager.cleanup()

    asyncio.run(invoke())
    assert _stderr_summaries(canary_harness)
    canary_harness.assert_no_canaries()


def test_stderr_content_is_discarded_and_summarised_once(canary_harness):
    canaries = canary_harness.canaries
    line = f"stderr canary: {canaries.prompt} {canaries.token}\n"
    stderr = mcp_obs.FilteredStderrPipe()
    try:
        for _ in range(3):
            stderr.stream.write(line)
            stderr.stream.flush()
    finally:
        stderr.close()

    (summary,) = _stderr_summaries(canary_harness)
    assert summary.levelno == logging.DEBUG
    assert summary.getMessage().endswith(f", {3 * len(line.encode())} bytes)")
    assert not any(
        record.name == mcp_obs.__name__ and record.levelno >= logging.WARNING
        for record in canary_harness.records
    )
    canary_harness.assert_no_canaries()


def test_empty_stderr_logs_nothing(caplog):
    caplog.set_level(logging.DEBUG, logger=mcp_obs.__name__)
    mcp_obs.FilteredStderrPipe().close()
    assert not [r for r in caplog.records if r.name == mcp_obs.__name__]


def test_untrusted_tool_name_never_becomes_a_span_name(canary_harness, telemetry):
    canaries = canary_harness.canaries
    tool_name = canaries.add("tool name", f"private-tool-{canaries.tag}")

    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config("external", [sys.executable, str(SERVER)])
        try:
            assert await manager.start_server("external")
            with pytest.raises(Exception):
                await manager.call_tool("external", tool_name, {})
        finally:
            await manager.cleanup()

    asyncio.run(invoke())
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.mcp.server"] == "external"
    assert span.attributes["canchat.mcp.tool"] == "other"
    assert tool_name not in span.to_json()
    canary_harness.assert_no_canaries()


def test_real_manager_call_cancellation_is_recorded(telemetry):
    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config(
            "time_server", [sys.executable, str(SERVER)], builtin=True
        )
        try:
            assert await manager.start_server("time_server")
            assert await manager.get_tools_from_server("time_server")
            task = asyncio.create_task(
                manager.call_tool(
                    "time_server",
                    "get_current_time",
                    {"payload": "safe", "reply": "safe", "mode": "wait"},
                )
            )
            await asyncio.sleep(0.1)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await manager.cleanup()

    asyncio.run(invoke())
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] == "cancelled"
    assert span.status.status_code == StatusCode.UNSET
    (point,) = telemetry.points()
    assert point.attributes["outcome"] == "cancelled"
    assert point.count == 1
    assert point.sum >= 0.1


def test_crew_adapter_cleans_up_after_failed_start(tmp_path):
    before = {
        thread.ident
        for thread in threading.enumerate()
        if thread.name in {"crew-mcp", "mcp-stderr"}
    }
    params = StdioServerParameters(
        command=sys.executable, args=[str(tmp_path / "missing-server.py")]
    )
    with pytest.raises(Exception):
        MCPServerAdapter(params, "time_server", connect_timeout=5)
    after = {
        thread.ident
        for thread in threading.enumerate()
        if thread.name in {"crew-mcp", "mcp-stderr"}
    }
    assert after <= before


def test_crew_tool_reports_a_crashed_subprocess(canary_harness, telemetry):
    canaries = canary_harness.canaries
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    adapter = MCPServerAdapter(params, "time_server")
    (tool,) = adapter.__enter__()
    done = threading.Event()
    failures = []

    def invoke():
        try:
            tool.run(payload=_payload(canaries), reply=canaries.response, mode="crash")
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    try:
        assert done.wait(10), "MCP tool call did not notice the server exit"
    finally:
        adapter.close()
        worker.join(timeout=5)

    assert failures
    assert not worker.is_alive()
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] == "error"
    assert span.status.status_code == StatusCode.ERROR
    (point,) = telemetry.points()
    assert point.attributes["outcome"] == "error"
    assert point.count == 1
    canary_harness.assert_no_canaries()


def test_crew_adapter_close_unblocks_an_inflight_tool(monkeypatch, telemetry):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    adapter = MCPServerAdapter(params, "time_server")
    (tool,) = adapter.__enter__()
    started = threading.Event()
    done = threading.Event()
    failures = []
    forward = adapter._stderr._forward

    def note_tool_started(chunk):
        forward(chunk)
        if b"MCP stderr:" in chunk:
            started.set()

    monkeypatch.setattr(adapter._stderr, "_forward", note_tool_started)

    def invoke():
        try:
            tool.run(payload="safe", reply="safe", mode="wait")
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    try:
        assert started.wait(10), "MCP tool did not start"
        adapter.close()
        assert done.wait(10), "MCP tool call did not unblock on shutdown"
    finally:
        adapter.close()
        worker.join(timeout=5)

    assert failures
    assert not worker.is_alive()
    assert not adapter._thread.is_alive()
    assert not adapter._stderr._thread.is_alive()
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] in {"cancelled", "error"}
    (point,) = telemetry.points()
    assert point.attributes["outcome"] == span.attributes["canchat.outcome"]
    assert point.count == 1


@pytest.mark.parametrize("endpoint", ["run_crew_query", "run_multi_server_crew_query"])
def test_http_crew_executors_preserve_the_calling_span(
    monkeypatch, telemetry, endpoint
):
    fake_integration = types.ModuleType("mcp_backend.integration.crew_mcp_integration")
    setattr(fake_integration, "CrewMCPManager", None)
    source = Path(__file__).resolve().parents[3] / "mcp_backend/routers/crew_mcp.py"
    spec = importlib.util.spec_from_file_location("obs29_crew_router", source)
    assert spec is not None
    assert spec.loader is not None
    router = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        imports.setitem(
            sys.modules,
            "mcp_backend.integration.crew_mcp_integration",
            fake_integration,
        )
        spec.loader.exec_module(router)

    observed_span = []

    class Manager:
        def get_available_tools(self):
            return [{"name": "get_current_time"}]

        def run_intelligent_crew(self, query, selected_tools, token):
            from opentelemetry import trace

            observed_span.append(trace.get_current_span().get_span_context().span_id)
            return "answer", {}, "time_server"

    async def token_for_request(_request):
        return "token"

    monkeypatch.setattr(router, "crew_mcp_manager", Manager())
    monkeypatch.setattr(router, "get_graph_access_token_for_user", token_for_request)
    request = router.CrewMCPQuery(query="safe", selected_tools=["time"])
    with telemetry.bundle.tracer.start_as_current_span("parent") as parent:
        result = asyncio.run(
            getattr(router, endpoint)(
                SimpleNamespace(), request, SimpleNamespace(id="user")
            )
        )

    assert result.success
    assert observed_span == [parent.get_span_context().span_id]


def _assert_timeout_telemetry(telemetry):
    (span,) = telemetry.tool_spans()
    assert span.attributes["canchat.outcome"] == "timeout"
    assert span.status.status_code == StatusCode.ERROR
    (point,) = telemetry.points()
    assert point.attributes["outcome"] == "timeout"
    assert point.count == 1


def test_real_manager_call_times_out(monkeypatch, telemetry):
    monkeypatch.setattr(mcp_manager, "MCP_TOOL_CALL_TIMEOUT", 0.5)

    async def invoke():
        manager = FastMCPManager()
        manager.add_server_config(
            "time_server", [sys.executable, str(SERVER)], builtin=True
        )
        try:
            assert await manager.start_server("time_server")
            with pytest.raises(Exception):
                await manager.call_tool(
                    "time_server",
                    "get_current_time",
                    {"payload": "safe", "reply": "safe", "mode": "wait"},
                )
        finally:
            await manager.cleanup()

    asyncio.run(invoke())
    _assert_timeout_telemetry(telemetry)


def test_crew_tool_call_times_out(telemetry):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    with MCPServerAdapter(params, "time_server", call_timeout=0.5) as tools:
        with pytest.raises(Exception):
            tools[0].run(payload="safe", reply="safe", mode="wait")
    _assert_timeout_telemetry(telemetry)


def test_observed_client_matches_pinned_fastmcp():
    upstream = inspect.signature(Client.call_tool_mcp).parameters
    observed = inspect.signature(mcp_manager.ObservedMCPClient.call_tool_mcp).parameters
    assert list(observed) == list(upstream)
    assert inspect.iscoroutinefunction(Client._await_with_session_monitoring)
