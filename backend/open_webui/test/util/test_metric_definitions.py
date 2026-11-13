import json
import traceback
from unittest.mock import ANY, Mock

import pytest

from open_webui.config import DEFAULT_METRIC_DEFINITIONS, _load_metric_definitions
from open_webui.metrics.service import MetricsService


def test_unset_uses_builtin_defaults(monkeypatch):
    monkeypatch.delenv("METRIC_DEFINITIONS", raising=False)

    # HTTP request metrics come from the FastAPI instrumentation (OBS-20).
    definitions = _load_metric_definitions()
    assert definitions is DEFAULT_METRIC_DEFINITIONS
    by_name = {d["name"]: d for d in definitions}
    assert "task_queue_size" not in by_name
    assert by_name["canchat.tasks.active"]["type"] == "observable_updowncounter"
    assert by_name["canchat.llm.requests"]["type"] == "counter"
    for name in (
        "canchat.llm.request.duration",
        "canchat.llm.time_to_first_token",
        "canchat.rag.retrieval.duration",
        "canchat.rag.embedding.duration",
        "canchat.rag.vector.duration",
        "canchat.mcp.tool.duration",
    ):
        assert by_name[name]["type"] == "histogram"
        assert by_name[name]["unit"] == "s"
        assert by_name[name]["buckets"]
    assert MetricsService(Mock(), definitions).list_instruments() == list(by_name)


@pytest.mark.parametrize(
    "metric_type",
    [
        "counter",
        "updowncounter",
        "histogram",
        "gauge",
        "observable_updowncounter",
        "COUNTER",
    ],
)
def test_valid_json_creates_instrument(monkeypatch, metric_type):
    definitions = [
        {
            "name": "custom_metric",
            "type": metric_type,
            "description": "Custom metric",
            "unit": "",
        }
    ]
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps(definitions))

    parsed = _load_metric_definitions()
    assert parsed == definitions
    meter = Mock()
    service = MetricsService(meter, parsed)
    assert service.list_instruments() == ["custom_metric"]
    method = {
        "counter": meter.create_counter,
        "updowncounter": meter.create_up_down_counter,
        "histogram": meter.create_histogram,
        "gauge": meter.create_observable_gauge,
        "observable_updowncounter": meter.create_observable_up_down_counter,
    }[metric_type.lower()]
    kwargs: dict[str, object] = {
        "name": "custom_metric",
        "description": "Custom metric",
        "unit": "",
    }
    if metric_type in {"gauge", "observable_updowncounter"}:
        kwargs["callbacks"] = [ANY]
    method.assert_called_once_with(**kwargs)


def test_buckets_and_attributes_are_accepted(monkeypatch):
    definition = {
        "name": "latency",
        "type": "histogram",
        "description": "",
        "unit": "s",
        "buckets": [0.1, 1, 10],
        "attributes": ["tier"],
    }
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    assert _load_metric_definitions() == [definition]


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"buckets": [1, 1]}, "buckets"),
        ({"buckets": [2, 1]}, "buckets"),
        ({"buckets": []}, "buckets"),
        ({"buckets": ["1"]}, "buckets"),
        ({"type": "counter", "buckets": [1, 2]}, "buckets"),
        ({"attributes": ["user_id"]}, "attributes"),
        ({"attributes": [""]}, "attributes"),
        ({"attributes": [1]}, "attributes"),
    ],
)
def test_invalid_buckets_or_attributes_are_rejected(monkeypatch, overrides, field):
    definition = {"name": "m", "type": "histogram", "description": "", "unit": ""}
    definition.update(overrides)
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    with pytest.raises(ValueError, match=rf"METRIC_DEFINITIONS\[0\].*{field}"):
        _load_metric_definitions()


def test_empty_array_is_allowed(monkeypatch):
    monkeypatch.setenv("METRIC_DEFINITIONS", "[]")
    assert _load_metric_definitions() == []


@pytest.mark.parametrize("value", ["", "not json", "[{", '[{"name": "test",}]'])
def test_invalid_json_has_clear_error(monkeypatch, value):
    monkeypatch.setenv("METRIC_DEFINITIONS", value)
    with pytest.raises(ValueError, match="METRIC_DEFINITIONS must be valid JSON"):
        _load_metric_definitions()


@pytest.mark.parametrize("value", [None, {}, "counter", 1, True])
def test_non_array_json_is_rejected(monkeypatch, value):
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps(value))
    with pytest.raises(ValueError, match="METRIC_DEFINITIONS must be a JSON array"):
        _load_metric_definitions()


@pytest.mark.parametrize("value", [None, {}, "counter", [], 1])
def test_non_definition_array_entry_is_rejected(monkeypatch, value):
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([value]))
    with pytest.raises(ValueError, match=r"Invalid METRIC_DEFINITIONS\[0\]"):
        _load_metric_definitions()


@pytest.mark.parametrize("field", ["name", "type", "description", "unit"])
@pytest.mark.parametrize("bad_value", [None, 1, True, [], {}])
def test_non_string_field_is_rejected(monkeypatch, field, bad_value):
    definition = {"name": "test", "type": "counter", "description": "", "unit": ""}
    definition[field] = bad_value
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    with pytest.raises(ValueError, match=rf"METRIC_DEFINITIONS\[0\].*{field}"):
        _load_metric_definitions()


@pytest.mark.parametrize("field", ["name", "type", "description", "unit"])
def test_missing_field_is_rejected(monkeypatch, field):
    definition = {"name": "test", "type": "counter", "description": "", "unit": ""}
    del definition[field]
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    with pytest.raises(ValueError, match=rf"METRIC_DEFINITIONS\[0\].*{field}"):
        _load_metric_definitions()


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_is_rejected(monkeypatch, name):
    definition = {
        "name": name,
        "type": "counter",
        "description": "",
        "unit": "",
    }
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    with pytest.raises(
        ValueError,
        match=r"METRIC_DEFINITIONS\[0\]\.name: expected a non-empty string",
    ):
        _load_metric_definitions()


def test_unsupported_type_reports_definition_index(monkeypatch):
    definition = {"name": "test", "type": "counter", "description": "", "unit": ""}
    monkeypatch.setenv(
        "METRIC_DEFINITIONS",
        json.dumps([definition, {**definition, "type": "unsupported"}]),
    )
    with pytest.raises(ValueError, match=r"METRIC_DEFINITIONS\[1\].type: expected"):
        _load_metric_definitions()


def test_empty_type_is_rejected(monkeypatch):
    definition = {"name": "test", "type": "", "description": "", "unit": ""}
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([definition]))
    with pytest.raises(ValueError, match=r"METRIC_DEFINITIONS\[0\].type: expected"):
        _load_metric_definitions()


def test_error_does_not_echo_definition_values(monkeypatch):
    sensitive_value = "sensitive-configuration-value"
    monkeypatch.setenv("METRIC_DEFINITIONS", json.dumps([{"name": sensitive_value}]))
    with pytest.raises(ValueError) as error:
        _load_metric_definitions()
    assert sensitive_value not in "".join(traceback.format_exception(error.value))
