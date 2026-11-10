import inspect
import logging

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from open_webui.metrics import service as service_module
from open_webui.metrics.service import (
    LLM_DURATION_BUCKETS,
    MetricAttributeError,
    MetricsDefinition,
    MetricsService,
    MetricUsageError,
)

DEFINITIONS = [
    {"name": "requests", "type": "counter", "description": "", "unit": "{request}"},
    {"name": "inflight", "type": "updowncounter", "description": "", "unit": "{op}"},
    {
        "name": "latency",
        "type": "histogram",
        "description": "",
        "unit": "s",
        "buckets": list(LLM_DURATION_BUCKETS),
    },
    {"name": "temperature", "type": "gauge", "description": "", "unit": "1"},
    {
        "name": "active",
        "type": "observable_updowncounter",
        "description": "",
        "unit": "{task}",
    },
    {
        "name": "custom_labels",
        "type": "counter",
        "description": "",
        "unit": "",
        "attributes": ["tier"],
    },
]


@pytest.fixture
def reader():
    return InMemoryMetricReader()


@pytest.fixture
def make_service(reader):
    provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    yield lambda strict: MetricsService(
        provider.get_meter("test"), DEFINITIONS, strict=strict
    )
    provider.shutdown()


def _points(reader):
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        metric.name: list(metric.data.data_points)
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_counter_and_updowncounter_record(reader, make_service):
    service = make_service(strict=True)
    service.record("requests", 1, {"model": "gpt", "outcome": "success"})
    service.record("requests", 2, {"model": "gpt", "outcome": "success"})
    service.record("inflight", 3)
    service.record("inflight", -1)

    points = _points(reader)
    (requests,) = points["requests"]
    assert requests.value == 3
    assert dict(requests.attributes) == {"model": "gpt", "outcome": "success"}
    assert points["inflight"][0].value == 2


def test_histogram_uses_configured_buckets(reader, make_service):
    service = make_service(strict=True)
    service.record("latency", 0.7, {"provider": "azure"})
    service.record("latency", 45)

    by_attrs = {
        tuple(sorted(point.attributes.items())): point
        for point in _points(reader)["latency"]
    }
    point = by_attrs[(("provider", "azure"),)]
    assert tuple(point.explicit_bounds) == tuple(float(b) for b in LLM_DURATION_BUCKETS)
    assert point.count == 1
    assert point.bucket_counts[1] == 1  # 0.5 < 0.7 <= 1
    assert by_attrs[()].bucket_counts[7] == 1  # 30 < 45 <= 60


@pytest.mark.parametrize("name", ["temperature", "active"])
def test_observable_callbacks_are_dispatched(reader, make_service, name):
    service = make_service(strict=True)
    service.register_callback(name, lambda: [(5, {"operation": "a"})])
    service.register_callback(name, lambda: [(7, {"operation": "b"})])

    values = {
        point.attributes["operation"]: point.value for point in _points(reader)[name]
    }
    assert values == {"a": 5, "b": 7}


def test_observable_without_sources_reports_nothing(reader, make_service):
    make_service(strict=True)
    assert "temperature" not in _points(reader)


def test_failing_callback_does_not_block_others(reader, make_service, caplog):
    service = make_service(strict=False)

    def broken():
        raise RuntimeError("boom")

    service.register_callback("temperature", broken)
    service.register_callback("temperature", lambda: [(1, None)])

    with caplog.at_level(logging.WARNING, logger=service_module.__name__):
        (point,) = _points(reader)["temperature"]
    assert point.value == 1
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize(
    "key",
    ["user_id", "session_id", "request_id", "chat_id", "trace_id", "span_id", "x"],
)
def test_disallowed_label_raises_in_strict_mode(make_service, key):
    service = make_service(strict=True)
    with pytest.raises(MetricAttributeError, match=key):
        service.record("requests", 1, {key: "value"})


def test_disallowed_label_is_dropped_with_one_warning(reader, make_service, caplog):
    service = make_service(strict=False)
    secret = "user-1234-secret"

    with caplog.at_level(logging.WARNING, logger=service_module.__name__):
        for _ in range(3):
            service.record("requests", 1, {"user_id": secret, "model": "gpt"})

    (point,) = _points(reader)["requests"]
    assert point.value == 3
    assert dict(point.attributes) == {"model": "gpt"}
    warnings = [r for r in caplog.records if "user_id" in r.getMessage()]
    assert len(warnings) == 1
    assert secret not in caplog.text


def test_observable_labels_are_filtered(reader, make_service):
    service = make_service(strict=False)
    service.register_callback("active", lambda: [(2, {"chat_id": "c1"})])

    (point,) = _points(reader)["active"]
    assert point.value == 2
    assert dict(point.attributes) == {}


def test_non_scalar_label_value_is_rejected(make_service):
    service = make_service(strict=True)
    with pytest.raises(MetricAttributeError, match="model"):
        service.record("requests", 1, {"model": {"nested": "value"}})


def test_per_metric_attributes_extend_allow_list(reader, make_service):
    service = make_service(strict=True)
    service.record("custom_labels", 1, {"tier": "gold"})
    with pytest.raises(MetricAttributeError):
        service.record("requests", 1, {"tier": "gold"})

    assert dict(_points(reader)["custom_labels"][0].attributes) == {"tier": "gold"}


def test_forbidden_label_cannot_be_allow_listed(make_service):
    service = make_service(strict=True)
    with pytest.raises(ValueError, match="attributes"):
        service.add_instrument(
            MetricsDefinition(
                name="bad",
                type="counter",
                description="",
                unit="",
                attributes=["session_id"],
            )
        )
    assert not service.has_instrument("bad")


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.record("missing", 1),
        lambda s: s.record("temperature", 1),
        lambda s: s.record("requests", -1),
        lambda s: s.register_callback("requests", lambda: []),
        lambda s: s.register_callback("missing", lambda: []),
    ],
)
def test_misuse_raises_in_strict_and_is_noop_otherwise(make_service, call, caplog):
    with pytest.raises(MetricUsageError):
        call(make_service(strict=True))

    with caplog.at_level(logging.WARNING, logger=service_module.__name__):
        call(make_service(strict=False))
    assert caplog.records


def test_strict_mode_defaults_from_environment(monkeypatch, make_service):
    monkeypatch.setenv("METRICS_STRICT", "false")
    assert MetricsService(None).strict is False
    monkeypatch.setenv("METRICS_STRICT", "true")
    assert MetricsService(None).strict is True


def test_invalid_definition_is_logged_not_raised(make_service, caplog):
    with caplog.at_level(logging.ERROR, logger=service_module.__name__):
        service = MetricsService(
            make_service(strict=True)._meter,
            [{"name": "bad", "type": "nope", "description": "", "unit": ""}],
        )
    assert service.list_instruments() == []
    assert "'bad'" in caplog.text


def test_service_module_has_no_print_calls():
    assert "print(" not in inspect.getsource(service_module)


def test_metrics_service_accessor_round_trips(make_service):
    service = make_service(True)
    previous = service_module.get_metrics_service()
    try:
        service_module.set_metrics_service(service)
        assert service_module.get_metrics_service() is service
        service_module.set_metrics_service(None)
        assert service_module.get_metrics_service() is None
    finally:
        service_module.set_metrics_service(previous)
