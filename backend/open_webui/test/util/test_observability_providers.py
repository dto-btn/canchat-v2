import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider

from open_webui.observability import providers as providers_module
from open_webui.observability.providers import (
    OTelSettings,
    ProviderBundle,
    create_providers,
    initialize_providers,
    load_settings,
)
from open_webui.retrieval.web import web_search_logger


def _settings(enabled: bool) -> OTelSettings:
    return OTelSettings(
        enabled=enabled,
        service_name="test-service",
        service_version="test",
        environment="test",
        endpoint="http://collector:4318",
        meter_name="test-meter",
        metric_export_interval_ms=15000,
    )


def _collected_metrics(reader: InMemoryMetricReader) -> dict:
    return {
        metric.name: metric
        for resource_metrics in reader.get_metrics_data().resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_otel_names_take_precedence_over_legacy_aliases():
    settings = load_settings(
        {
            "ENV": "prod",
            "OTEL_ENABLED": "true",
            "INSTRUMENTATION_SERVICE_NAME": "legacy-service",
            "OTEL_SERVICE_NAME": "current-service",
            "INSTRUMENTATION_ENDPOINT": "http://legacy:4318/v1/metrics",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://current:4318",
            "INSTRUMENTATION_EXPORT_INTERVAL_MS": "5000",
            "OTEL_METRIC_EXPORT_INTERVAL": "12000",
        }
    )

    assert settings.enabled is True
    assert settings.service_name == "current-service"
    assert settings.endpoint == "http://current:4318"
    assert settings.metric_export_interval_ms == 12000


@pytest.mark.parametrize(
    "legacy_endpoint",
    [
        "http://collector:4318/v1/metrics",
        "http://collector:4318/v1/metrics/",
        "http://collector:4318",
    ],
)
def test_legacy_aliases_are_used_and_metric_path_is_removed(legacy_endpoint):
    settings = load_settings(
        {
            "ENV": "prod",
            "INSTRUMENTATION_SERVICE_NAME": "legacy-service",
            "INSTRUMENTATION_ENDPOINT": legacy_endpoint,
            "INSTRUMENTATION_EXPORT_INTERVAL_MS": "5000",
        }
    )

    assert settings.enabled is True
    assert settings.service_name == "legacy-service"
    assert settings.endpoint == "http://collector:4318"
    assert settings.metric_export_interval_ms == 5000


def test_defaults_match_configuration_reference():
    settings = load_settings({"ENV": "prod"})

    assert settings.service_name == "canchat-backend"
    assert settings.endpoint == "http://otel-collector:4318"
    assert settings.metric_export_interval_ms == 15000
    assert settings.environment == "prod"


def test_local_development_disables_otel_by_default():
    assert load_settings({"ENV": "dev"}).enabled is False
    assert load_settings({"ENV": "prod"}).enabled is True
    assert load_settings({"ENV": "dev", "DOCKER": "true"}).enabled is True
    assert load_settings({"ENV": "prod", "OTEL_ENABLED": "false"}).enabled is False


@pytest.mark.parametrize(
    "values, message",
    [
        ({"OTEL_ENABLED": "yes"}, "OTEL_ENABLED"),
        ({"OTEL_METRIC_EXPORT_INTERVAL": "soon"}, "OTEL_METRIC_EXPORT_INTERVAL"),
        ({"INSTRUMENTATION_EXPORT_INTERVAL_MS": "0"}, "OTEL_METRIC_EXPORT_INTERVAL"),
        ({"OTEL_SERVICE_NAME": ""}, "OTEL_SERVICE_NAME"),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": ""}, "OTEL_EXPORTER_OTLP_ENDPOINT"),
    ],
)
def test_invalid_settings_fail_clearly(values, message):
    with pytest.raises(ValueError, match=message):
        load_settings({"ENV": "prod", **values})


def test_disabled_otel_creates_no_exporters(monkeypatch):
    def unexpected_exporter(*args, **kwargs):
        raise AssertionError("OTLP exporter created while OTEL_ENABLED=false")

    for name in ("OTLPMetricExporter", "OTLPSpanExporter", "OTLPLogExporter"):
        monkeypatch.setattr(providers_module, name, unexpected_exporter)

    bundle = create_providers(_settings(enabled=False))
    try:
        bundle.meter.create_counter("disabled_total").add(1)
        with bundle.tracer.start_as_current_span("disabled"):
            pass
    finally:
        bundle.shutdown()


def test_enabled_otel_uses_signal_endpoints(monkeypatch):
    endpoints = {}

    def recording_exporter(signal, base):
        class Exporter(base):
            def __init__(self, *, endpoint):
                endpoints[signal] = endpoint
                super().__init__(endpoint=endpoint)

        return Exporter

    for signal, name in (
        ("metrics", "OTLPMetricExporter"),
        ("traces", "OTLPSpanExporter"),
        ("logs", "OTLPLogExporter"),
    ):
        monkeypatch.setattr(
            providers_module,
            name,
            recording_exporter(signal, getattr(providers_module, name)),
        )

    bundle = create_providers(_settings(enabled=True))
    # Nothing was recorded, so shutdown does not attempt an export.
    bundle.meter_provider.shutdown()
    bundle.tracer_provider.shutdown()
    bundle.logger_provider.shutdown()

    assert endpoints == {
        "metrics": "http://collector:4318/v1/metrics",
        "traces": "http://collector:4318/v1/traces",
        "logs": "http://collector:4318/v1/logs",
    }
    resource = bundle.tracer_provider.resource.attributes
    assert resource["service.name"] == "test-service"
    assert resource["service.version"] == "test"
    assert resource["deployment.environment"] == "test"


def test_web_search_metrics_are_recorded_with_in_memory_reader(monkeypatch):
    reader = InMemoryMetricReader()
    providers = create_providers(_settings(enabled=False), metric_readers=[reader])
    monkeypatch.setattr(
        web_search_logger,
        "_METRICS_INSTRUMENTS",
        web_search_logger._create_metrics_instruments(providers.meter),
    )

    try:
        context = web_search_logger.log_web_search_dispatch(
            engine="test-engine",
            query="test query",
            result_count_requested=3,
        )
        web_search_logger.log_web_search_result(
            dispatch_ctx=context,
            results=[types.SimpleNamespace(link="https://example.com/a")],
        )
        observed = _collected_metrics(reader)
    finally:
        providers.shutdown()

    assert observed["web_search_dispatch_total"].data.data_points[0].value == 1
    assert observed["web_search_result_total"].data.data_points[0].value == 1
    assert "web_search_result_latency_ms" in observed
    assert observed["web_search_result_count"].data.data_points[0].sum == 1


def test_bundle_flushes_before_shutting_down_each_provider():
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def force_flush(self, timeout_millis):
            calls.append((self.name, "flush"))
            return True

        def shutdown(self):
            calls.append((self.name, "shutdown"))

    bundle = ProviderBundle(
        meter_provider=Provider("meter"),
        tracer_provider=Provider("tracer"),
        logger_provider=Provider("logger"),
        meter=None,
        tracer=None,
    )
    bundle.shutdown()

    assert calls == [
        ("logger", "flush"),
        ("logger", "shutdown"),
        ("tracer", "flush"),
        ("tracer", "shutdown"),
        ("meter", "flush"),
        ("meter", "shutdown"),
    ]


def test_process_providers_shut_down_once(monkeypatch):
    bundle = mock.Mock()
    monkeypatch.setattr(providers_module, "_providers", bundle)
    monkeypatch.setattr(providers_module, "_providers_shutdown", False)

    providers_module.shutdown_providers()
    providers_module.shutdown_providers()

    bundle.shutdown.assert_called_once_with()


def test_provider_initialization_is_idempotent():
    assert initialize_providers() is initialize_providers()


def test_providers_created_after_crew_integration_import_are_enabled(monkeypatch):
    stubs = {
        "crewai": types.ModuleType("crewai"),
        "crewai_tools": types.ModuleType("crewai_tools"),
        "mcp": types.ModuleType("mcp"),
    }
    for name in ("Agent", "Task", "Crew", "Process", "LLM"):
        setattr(stubs["crewai"], name, object)
    stubs["crewai_tools"].MCPServerAdapter = object
    stubs["mcp"].StdioServerParameters = object
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    source = (
        Path(__file__).resolve().parents[3]
        / "mcp_backend/integration/crew_mcp_integration.py"
    )
    spec = importlib.util.spec_from_file_location("obs14_crew_integration", source)
    module = importlib.util.module_from_spec(spec)

    with mock.patch.dict(os.environ):
        for name in (
            "OTEL_SDK_DISABLED",
            "CREWAI_DISABLE_TELEMETRY",
            "CREWAI_TRACING_ENABLED",
        ):
            os.environ.pop(name, None)
        spec.loader.exec_module(module)

        assert os.environ.get("OTEL_SDK_DISABLED") is None
        assert os.environ.get("CREWAI_DISABLE_TELEMETRY") == "true"
        assert os.environ.get("CREWAI_TRACING_ENABLED") == "false"

        reader = InMemoryMetricReader()
        meter_provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
        tracer_provider = TracerProvider(shutdown_on_exit=False)
        try:
            meter_provider.get_meter("obs14").create_counter("after_crew_total").add(1)
            with tracer_provider.get_tracer("obs14").start_as_current_span(
                "after-crew"
            ) as span:
                assert span.is_recording()
            assert "after_crew_total" in _collected_metrics(reader)
        finally:
            meter_provider.shutdown()
            tracer_provider.shutdown()
