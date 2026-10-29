"""OBS-15: central logging configuration, log record contract and logger policy."""

import copy
import importlib
import io
import json
import logging
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

# Other test modules stub "uvicorn" at collection time; custom_logging needs the
# real uvicorn.logging module, so load it without replacing the stub.
_uvicorn_stub = sys.modules.get("uvicorn")
if _uvicorn_stub is not None and not hasattr(_uvicorn_stub, "__path__"):
    del sys.modules["uvicorn"]
    importlib.import_module("uvicorn.logging")
    sys.modules["uvicorn"] = _uvicorn_stub

from open_webui import custom_logging  # noqa: E402
from open_webui.observability import logging_config
from open_webui.observability.context import log_context
from open_webui.observability.logging_config import (
    ContractOTLPHandler,
    JsonFormatter,
    LogContextFilter,
    RateLimitFilter,
    configure_logging,
    load_log_settings,
    parse_rate_limit,
)
from open_webui.observability.redaction import MESSAGE_OMITTED, RedactionFilter

BACKEND_DIR = Path(__file__).resolve().parents[3]
RESOURCE = {
    "service.name": "canchat-backend",
    "service.version": "1.2.3",
    "deployment.environment": "test",
}
SOURCE_LEVELS = {"AUDIT": "INFO", "MCP": "INFO", "MAIN": "INFO", "DB": "INFO"}
MANAGED_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "open_webui.audit",
    "open_webui.audit.content",
    "mcp_backend",
    *logging_config.NOISY_LOGGERS,
)


@pytest.fixture(autouse=True)
def enabled_otel_sdk(monkeypatch):
    """Keep local test providers recording even when the shell disables OTel."""
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)


@pytest.fixture
def isolated_logging(monkeypatch):
    """Restore every logger touched by the policy; yields the root logger.

    pytest adds and removes its own root handlers around each phase, so root
    handlers are not saved; only handlers installed by configure_logging are
    removed afterwards.
    """
    root = logging.getLogger()
    root_level = root.level
    saved = {}
    for name in MANAGED_LOGGERS:
        lg = logging.getLogger(name)
        saved[name] = (
            lg.level,
            lg.propagate,
            lg.disabled,
            list(lg.handlers),
            list(lg.filters),
        )
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
    monkeypatch.setattr(logging_config, "_state", logging_config._LoggingState())
    monkeypatch.setattr(logging_config, "_TEXT_FORMATTERS", {})
    monkeypatch.setattr(logging_config, "_atexit_registered", True)
    yield root
    for handler in list(root.handlers):
        if logging_config._is_owned(handler):
            root.removeHandler(handler)
    root.setLevel(root_level)
    for name, (level, propagate, disabled, handlers, filters) in saved.items():
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled
        lg.handlers[:] = handlers
        lg.filters[:] = filters


def _configure(global_level, source_levels, environ):
    """configure_logging on a root logger without pytest's capture handlers."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if not logging_config._is_owned(handler):
            root.removeHandler(handler)
    return configure_logging(global_level, source_levels, environ)


def _record(
    msg="hello %s",
    args=("world",),
    level=logging.INFO,
    name="open_webui.t",
    exc_info=None,
    **extra,
):
    record = logging.LogRecord(name, level, "/app/module.py", 42, msg, args, exc_info)
    record.module = "module"
    record.__dict__.update(extra)
    return record


def _json(record, resource=RESOURCE):
    LogContextFilter().filter(record)
    return json.loads(JsonFormatter(resource).format(record))


def _capture_stdout(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    return stream


# ── Formatter ────────────────────────────────────────────────────────────────


def test_json_formatter_emits_all_contract_fields():
    tracer = TracerProvider().get_tracer("obs15")
    with log_context(request_id="req-12345678", operation_id="op-1", user_id="user-1"):
        with log_context(
            chat_id="chat-1", message_id="msg-1", model="gpt", provider="azure"
        ):
            with tracer.start_as_current_span("span") as span:
                fields = _json(_record(event="llm.first_token"))
                context = span.get_span_context()

    assert fields == {
        "timestamp": fields["timestamp"],
        "severity_text": "INFO",
        "severity_number": 9,
        "body": "hello world",
        "logger": "open_webui.t",
        "service.name": "canchat-backend",
        "service.version": "1.2.3",
        "deployment.environment": "test",
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
        "trace_flags": format(int(context.trace_flags), "02x"),
        "request_id": "req-12345678",
        "operation_id": "op-1",
        "user_id": "user-1",
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "model": "gpt",
        "provider": "azure",
        "event": "llm.first_token",
        "source": "module:42",
    }
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", fields["timestamp"])


def test_json_formatter_exception_fields():
    detail = "bad value"
    try:
        raise ValueError(detail)
    except ValueError:
        fields = _json(_record("failed", (), logging.ERROR, exc_info=sys.exc_info()))

    assert fields["severity_text"] == "ERROR"
    assert fields["severity_number"] == 17
    assert fields["exception.type"] == "ValueError"
    # OBS-18: exception messages are never emitted, even without the filter.
    assert "exception.message" not in fields
    assert "bad value" not in json.dumps(fields)
    assert fields["exception.stacktrace"].startswith(
        "Traceback (most recent call last)"
    )
    assert "test_json_formatter_exception_fields" in fields["exception.stacktrace"]
    assert (
        fields["exception.stacktrace"]
        .rstrip()
        .endswith("ValueError: [message omitted]")
    )


def test_json_formatter_omits_missing_context():
    fields = _json(_record(), resource={})

    assert set(fields) == {
        "timestamp",
        "severity_text",
        "severity_number",
        "body",
        "logger",
        "source",
    }


def test_explicit_record_fields_take_precedence_over_bound_context():
    with log_context(user_id="bound-user"):
        fields = _json(_record(user_id="explicit-user"))
    assert fields["user_id"] == "explicit-user"


@pytest.mark.parametrize(
    "level, text, number",
    [
        (logging.DEBUG, "DEBUG", 5),
        (logging.WARNING, "WARN", 13),
        (logging.CRITICAL, "FATAL", 21),
        (25, "Level 25", 12),
    ],
)
def test_severity_mapping(level, text, number):
    fields = _json(_record(level=level))
    assert (fields["severity_text"], fields["severity_number"]) == (text, number)


def test_each_record_is_one_json_line():
    output = JsonFormatter(RESOURCE).format(_record("line1\nline2\r\n\u2028", ()))
    assert "\n" not in output
    assert json.loads(output)["body"] == "line1\nline2\r\n\u2028"


def test_formatter_ignores_unlisted_extras():
    fields = _json(_record(prompt="CANARY-PROMPT", user_email="canary@example.invalid"))
    assert "CANARY" not in json.dumps(fields)
    assert "canary@example.invalid" not in json.dumps(fields)


# ── Settings ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"ENV": "dev"}, "text"),
        ({}, "text"),
        ({"ENV": "prod"}, "json"),
        ({"ENV": "dev", "DOCKER": "true"}, "json"),
        ({"ENV": "prod", "LOG_FORMAT": "TEXT"}, "text"),
        ({"ENV": "dev", "LOG_FORMAT": "json"}, "json"),
    ],
)
def test_log_format_defaults(values, expected):
    assert load_log_settings(values).format == expected


@pytest.mark.parametrize("value", ["", "xml", "jsonl"])
def test_invalid_log_format_fails_clearly(value):
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        load_log_settings({"LOG_FORMAT": value})


@pytest.mark.parametrize(
    "value, expected",
    [
        ("100/10s", (100, 10.0)),
        ("5/1", (5, 1.0)),
        ("20 / 2m", (20, 120.0)),
        ("1/0.5s", (1, 0.5)),
        ("0", (0, 0.0)),
    ],
)
def test_parse_rate_limit(value, expected):
    assert parse_rate_limit(value) == expected


@pytest.mark.parametrize(
    "value", ["", "100", "100/0s", "0/10s", "-1/10s", "ten/10s", "100/10d"]
)
def test_invalid_rate_limit_fails_clearly(value):
    with pytest.raises(ValueError, match="LOG_RATE_LIMIT"):
        parse_rate_limit(value)


def test_default_rate_limit():
    settings = load_log_settings({})
    assert (settings.rate_limit, settings.rate_period) == (100, 10.0)


# ── Rate limiter ─────────────────────────────────────────────────────────────


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def summaries():
    records = []
    handler = logging.Handler()
    # Snapshot: root handlers further down may redact the shared record in place.
    handler.emit = lambda record: records.append(copy.copy(record))
    summary_logger = logging.getLogger(logging_config.SUMMARY_LOGGER_NAME)
    summary_logger.addHandler(handler)
    yield records
    summary_logger.removeHandler(handler)


def _allowed(log_filter, count, name="open_webui.loop"):
    return sum(log_filter.filter(_record(name=name)) for _ in range(count))


def test_rate_limiter_drops_burst_and_emits_one_summary(summaries):
    clock = _Clock()
    limiter = RateLimitFilter(100, 10, clock=clock)

    assert _allowed(limiter, 150) == 100
    assert summaries == []

    clock.now += 10
    assert limiter.filter(_record(name="open_webui.loop"))
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.levelno == logging.WARNING
    assert summary.event == "log.rate_limited"
    assert summary.getMessage() == (
        "Dropped 50 log records from logger 'open_webui.loop' (rate limit 100 per 10s)"
    )

    clock.now += 10
    _allowed(limiter, 5)
    assert len(summaries) == 1


def test_rate_limiter_continuous_flood_summarizes_once_per_period(summaries):
    clock = _Clock()
    limiter = RateLimitFilter(10, 10, clock=clock)
    allowed = 0
    for _ in range(100):
        allowed += _allowed(limiter, 10)
        clock.now += 0.25

    # 10 initial tokens, then one token per second for 24.75 seconds.
    assert allowed == 34
    assert len(summaries) == 2
    pending = sum(b.dropped for b in limiter._pending.values())
    assert sum(r.args[0] for r in summaries) + pending == 1000 - allowed


def test_rate_limiter_is_per_logger_and_exempts_access_logs(summaries):
    limiter = RateLimitFilter(2, 10, clock=_Clock())

    assert _allowed(limiter, 5, "open_webui.a") == 2
    assert _allowed(limiter, 5, "open_webui.b") == 2
    assert _allowed(limiter, 50, "uvicorn.access") == 50


def test_rate_limiter_decision_is_shared_between_handlers(summaries):
    limiter = RateLimitFilter(1, 10, clock=_Clock())
    first, second = _record(), _record()

    assert limiter.filter(first) and limiter.filter(first)
    assert not limiter.filter(second) and not limiter.filter(second)
    assert limiter._buckets["open_webui.t"].dropped == 1


def test_rate_limit_summary_does_not_inherit_triggering_context():
    # Format at emit time, as the root handlers do, so context is read in place.
    lines = []
    handler = logging.Handler()
    handler.addFilter(LogContextFilter())
    handler.setFormatter(JsonFormatter(RESOURCE))
    handler.emit = lambda record: lines.append(json.loads(handler.format(record)))
    summary_logger = logging.getLogger(logging_config.SUMMARY_LOGGER_NAME)
    summary_logger.addHandler(handler)
    clock = _Clock()
    limiter = RateLimitFilter(1, 10, clock=clock)
    try:
        with log_context(user_id="user-A", request_id="req-AAAAAAAA"):
            _allowed(limiter, 3, "open_webui.flood")

        clock.now += 10
        tracer = TracerProvider().get_tracer("obs15")
        with log_context(user_id="user-B", request_id="req-BBBBBBBB"):
            with tracer.start_as_current_span("other-request"):
                assert limiter.filter(_record(name="open_webui.other"))
    finally:
        summary_logger.removeHandler(handler)

    assert len(lines) == 1
    assert lines[0]["body"].startswith(
        "Dropped 2 log records from logger 'open_webui.flood'"
    )
    leaked = {"user_id", "request_id", "trace_id", "span_id", "trace_flags"}
    assert leaked & set(lines[0]) == set()


def test_pending_summaries_are_reported_at_shutdown(summaries):
    limiter = RateLimitFilter(1, 10, clock=_Clock())
    _allowed(limiter, 4)

    limiter.emit_pending_summaries()

    assert [r.args[0] for r in summaries] == [3]


def test_rate_limit_zero_disables_limiter(isolated_logging, monkeypatch):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "text", "LOG_RATE_LIMIT": "0"})
    handler = isolated_logging.handlers[0]

    assert not any(isinstance(f, RateLimitFilter) for f in handler.filters)
    log = logging.getLogger("open_webui.flood")
    for i in range(500):
        log.info("line %d", i)
    assert stream.getvalue().count("line ") == 500


def test_rate_limit_applies_through_configured_handler(isolated_logging, monkeypatch):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json", "LOG_RATE_LIMIT": "3/1h"})
    log = logging.getLogger("open_webui.flood")
    for i in range(10):
        log.error("line %d", i)
    logging_config.shutdown_logging()

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["body"] for line in lines[:3]] == ["line 0", "line 1", "line 2"]
    assert lines[3]["event"] == "log.rate_limited"
    assert lines[3]["body"].startswith(
        "Dropped 7 log records from logger 'open_webui.flood'"
    )
    assert len(lines) == 4


# ── Logger policy ────────────────────────────────────────────────────────────


def test_audit_records_do_not_reach_root_or_stderr(
    isolated_logging, monkeypatch, capsys
):
    stream = _capture_stdout(monkeypatch)
    _configure("DEBUG", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    root_records = []
    spy = logging.Handler()
    spy.emit = root_records.append
    isolated_logging.addHandler(spy)
    try:
        for name in logging_config.AUDIT_LOGGER_NAMES:
            logging.getLogger(name).critical("AUDIT-CANARY")
        logging.getLogger("open_webui.audit.content.child").error("AUDIT-CANARY")
    finally:
        isolated_logging.removeHandler(spy)

    assert root_records == []
    assert "AUDIT-CANARY" not in stream.getvalue()
    assert "AUDIT-CANARY" not in capsys.readouterr().err
    for name in logging_config.AUDIT_LOGGER_NAMES:
        assert logging.getLogger(name).propagate is False


def test_source_levels_and_noisy_logger_pinning(isolated_logging, monkeypatch):
    _capture_stdout(monkeypatch)
    _configure(
        "DEBUG",
        {**SOURCE_LEVELS, "AUDIT": "ERROR", "MCP": "DEBUG"},
        {"LOG_FORMAT": "text", "DB_LOG_LEVEL": "INFO", "MAIN_LOG_LEVEL": "ERROR"},
    )

    assert logging.getLogger("open_webui.audit").level == logging.ERROR
    assert logging.getLogger("mcp_backend").level == logging.DEBUG
    assert logging.getLogger("sqlalchemy.engine").level == logging.INFO
    # An explicit level above WARNING does not lower the pin.
    assert logging.getLogger("uvicorn.error").level == logging.WARNING
    for name in (
        "httpx",
        "httpcore",
        "aiohttp",
        "LiteLLM",
        "litellm",
        "crewai",
        "peewee",
    ):
        assert logging.getLogger(name).level == logging.WARNING, name


def test_third_party_handlers_are_routed_through_root(isolated_logging, monkeypatch):
    stream = _capture_stdout(monkeypatch)
    stderr = io.StringIO()
    litellm = logging.getLogger("LiteLLM")
    litellm.addHandler(logging.StreamHandler(stderr))
    uvicorn = logging.getLogger("uvicorn")
    uvicorn.addHandler(logging.StreamHandler(stderr))
    uvicorn.propagate = False

    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    litellm.warning("from litellm")
    logging.getLogger("uvicorn.error").error("from uvicorn")

    assert stderr.getvalue() == ""
    bodies = [json.loads(line)["body"] for line in stream.getvalue().splitlines()]
    assert bodies == ["from litellm", "from uvicorn"]


def test_configure_logging_is_idempotent(isolated_logging, monkeypatch):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "text"})
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})

    assert len(isolated_logging.handlers) == 1
    logging.getLogger("open_webui.once").info("once")
    assert len(stream.getvalue().splitlines()) == 1
    assert json.loads(stream.getvalue())["body"] == "once"


def test_text_format_keeps_basic_format(isolated_logging, monkeypatch):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "text"})
    logging.getLogger("open_webui.text").warning("plain %s", "text")
    assert stream.getvalue() == "WARNING:open_webui.text:plain text\n"


def test_existing_entrypoint_configuration_is_kept(isolated_logging):
    script_handler = logging.StreamHandler(io.StringIO())
    isolated_logging.addHandler(script_handler)
    try:
        configured = configure_logging("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
        handlers = list(isolated_logging.handlers)
    finally:
        isolated_logging.removeHandler(script_handler)

    assert configured is False
    assert script_handler in handlers
    assert not any(logging_config._is_owned(h) for h in handlers)
    # The audit policy still applies.
    assert logging.getLogger("open_webui.audit").propagate is False


def test_preconfigured_root_handler_gets_redaction(isolated_logging):
    stream = io.StringIO()
    script_handler = logging.StreamHandler(stream)
    script_handler.setFormatter(logging.Formatter("%(levelname)s|%(message)s"))
    for handler in list(isolated_logging.handlers):
        if not logging_config._is_owned(handler):
            isolated_logging.removeHandler(handler)
    isolated_logging.addHandler(script_handler)
    try:
        configure_logging("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
        # Reconfiguring does not stack filters.
        configure_logging("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
        try:
            raise ValueError("prompt text from provider error")
        except ValueError as error:
            logging.getLogger("open_webui.preconfigured").exception(error)
    finally:
        isolated_logging.removeHandler(script_handler)

    output = stream.getvalue()
    assert "prompt text" not in output
    assert output.startswith("ERROR|ValueError\n")
    assert f"ValueError: {MESSAGE_OMITTED}" in output
    assert not logging_config._is_owned(script_handler)
    assert sum(isinstance(f, RedactionFilter) for f in script_handler.filters) == 1


# ── OTLP handler ─────────────────────────────────────────────────────────────


@pytest.fixture
def otlp_bundle(monkeypatch):
    exporter = InMemoryLogExporter()
    provider = LoggerProvider(
        resource=Resource.create(RESOURCE), shutdown_on_exit=False
    )
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    bundle = types.SimpleNamespace(logger_provider=provider, export_enabled=True)
    monkeypatch.setattr(logging_config, "initialize_providers", lambda: bundle)
    yield exporter
    provider.shutdown()


def test_otlp_handler_shares_filter_chain_and_fields(
    isolated_logging, monkeypatch, otlp_bundle
):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json", "LOG_RATE_LIMIT": "2/1h"})
    stdout_handler, otlp_handler = isolated_logging.handlers

    assert isinstance(otlp_handler, ContractOTLPHandler)
    shared = [f for f in stdout_handler.filters]
    assert all(f in otlp_handler.filters for f in shared)

    log = logging.getLogger("open_webui.otlp")
    with log_context(request_id="req-12345678", user_id="user-1"):
        log.warning("first %s", "record", extra={"event": "x.y", "prompt": "CANARY"})
        log.warning("second")
        log.warning("dropped")
    logging.getLogger("opentelemetry.exporter.test").error("exporter failure")

    json_lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    exported = otlp_bundle.get_finished_logs()
    assert [line["body"] for line in json_lines] == [
        "first record",
        "second",
        "exporter failure",
    ]
    assert [e.log_record.body for e in exported] == ["first record", "second"]

    record = exported[0].log_record
    attributes = dict(record.attributes)
    expected = {
        k: v
        for k, v in json_lines[0].items()
        if k
        not in {
            "timestamp",
            "severity_text",
            "severity_number",
            "body",
            "logger",
            *RESOURCE,
        }
    }
    assert attributes == expected
    assert record.severity_text == json_lines[0]["severity_text"] == "WARN"
    assert record.severity_number.value == json_lines[0]["severity_number"]
    assert "CANARY" not in json.dumps(attributes)


def test_stdout_and_otlp_handlers_redact_messages_and_exceptions(
    isolated_logging, monkeypatch, otlp_bundle
):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    for handler in isolated_logging.handlers:
        assert any(isinstance(f, RedactionFilter) for f in handler.filters)

    secret = "provider echoed the prompt"
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        logging.getLogger("open_webui.redact").exception(
            "call to %s failed", "https://api.example/v1?api-key=abc123456"
        )

    line = json.loads(stream.getvalue())
    record = otlp_bundle.get_finished_logs()[0].log_record
    attributes = dict(record.attributes)
    expected_body = "call to https://api.example/v1?[REDACTED] failed"
    assert line["body"] == record.body == expected_body
    for fields in (line, attributes):
        assert fields["exception.type"] == "RuntimeError"
        assert "exception.message" not in fields
        assert "test_stdout_and_otlp_handlers_redact" in fields["exception.stacktrace"]
        assert secret not in json.dumps(fields)
        assert "abc123456" not in json.dumps(fields)


def test_shutdown_logging_detaches_otlp_handler(
    isolated_logging, monkeypatch, otlp_bundle
):
    _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    assert len(isolated_logging.handlers) == 2

    logging_config.shutdown_logging()

    assert [type(h) for h in isolated_logging.handlers] == [
        logging_config._StdoutHandler
    ]
    logging.getLogger("open_webui.after").warning("after shutdown")
    assert otlp_bundle.get_finished_logs() == ()


def test_no_otlp_handler_when_export_disabled(isolated_logging, monkeypatch):
    _capture_stdout(monkeypatch)
    bundle = types.SimpleNamespace(
        logger_provider=LoggerProvider(shutdown_on_exit=False), export_enabled=False
    )
    monkeypatch.setattr(logging_config, "initialize_providers", lambda: bundle)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    assert [type(h) for h in isolated_logging.handlers] == [
        logging_config._StdoutHandler
    ]


# ── Access log, SQLAlchemy and process-level checks ──────────────────────────


def test_uvicorn_access_logger_is_suppressed_in_json_mode(
    isolated_logging, monkeypatch
):
    _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "json"})
    custom_logging.reconfigure_access_log()
    custom_logging.reconfigure_access_log()
    access = logging.getLogger("uvicorn.access")
    assert access.disabled is True
    assert access.handlers == []
    assert access.propagate is False


def test_uvicorn_access_logger_is_suppressed_in_text_mode(
    isolated_logging, monkeypatch
):
    stream = _capture_stdout(monkeypatch)
    _configure("INFO", SOURCE_LEVELS, {"LOG_FORMAT": "text"})
    custom_logging.reconfigure_access_log()
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.1:5555", "POST", "/api/x?q=1", "1.1", 201
    )
    assert stream.getvalue() == ""


def test_sqlalchemy_engine_never_logs_bound_parameters(caplog):
    # Integration tests replace db.engine, so check how db.py builds every
    # engine, then prove the option's effect on an engine built the same way.
    from sqlalchemy import create_engine, text

    from open_webui.internal import db

    source = Path(db.__file__).read_text()
    assert db.ENGINE_OPTIONS == {"hide_parameters": True}
    assert source.count("create_engine(\n") == source.count("**ENGINE_OPTIONS,") == 3
    migrations = (BACKEND_DIR / "open_webui/migrations/env.py").read_text()
    assert "hide_parameters=True" in migrations

    engine = create_engine("sqlite://", **db.ENGINE_OPTIONS)
    canary = "CANARY-BOUND-PARAMETER"
    try:
        with caplog.at_level(logging.INFO, logger="sqlalchemy.engine"):
            with engine.connect() as connection:
                connection.execute(text("SELECT :value"), {"value": canary})
                with pytest.raises(Exception) as error:
                    connection.execute(
                        text("SELECT * FROM no_such_table WHERE x = :v"), {"v": canary}
                    )
    finally:
        engine.dispose()
    assert "SQL parameters hidden due to hide_parameters=True" in caplog.text
    assert canary not in caplog.text
    assert canary not in str(error.value)


def test_in_process_migrations_keep_application_logging(monkeypatch, tmp_path, capsys):
    import logging.config as std_logging_config

    import open_webui.env
    from open_webui.internal import db

    monkeypatch.setattr(
        open_webui.env, "DATABASE_URL", f"sqlite:///{tmp_path / 'migrations.db'}"
    )
    calls = []
    monkeypatch.setattr(
        std_logging_config, "fileConfig", lambda *a, **k: calls.append(a)
    )
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    db.run_migrations()

    assert calls == []
    assert (list(root.handlers), root.level) == (handlers, level)
    assert "Error:" not in capsys.readouterr().out
    assert (tmp_path / "migrations.db").stat().st_size > 0


def _logging_config_offenders(backend_dir):
    offenders = []
    for package in ("open_webui", "mcp_backend"):
        for path in (backend_dir / package).rglob("*.py"):
            relative = path.relative_to(backend_dir).as_posix()
            if relative.startswith(("open_webui/test/", "mcp_backend/servers/")):
                continue
            if re.search(
                r"logging\.basicConfig\(|fileConfig\(|dictConfig\(", path.read_text()
            ):
                offenders.append(relative)
    return sorted(offenders)


def test_no_in_process_basic_config_calls():
    # The alembic CLI entrypoint keeps fileConfig, guarded for in-process runs.
    assert _logging_config_offenders(BACKEND_DIR) == ["open_webui/migrations/env.py"]


def test_logging_config_scan_only_checks_in_process_source(tmp_path):
    in_process = (
        "open_webui/env.py",
        "open_webui/routers/chat.py",
        "mcp_backend/integration/example.py",
    )
    excluded = (
        ".venv/bin/debug.py",
        ".venv/lib/python3.12/site-packages/example.py",
        "scripts/example.py",
        "mcp_backend/servers/example.py",
        "open_webui/test/example.py",
    )
    for relative in (*in_process, *excluded):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("logging.basicConfig()\n")

    assert _logging_config_offenders(tmp_path) == sorted(in_process)


def test_env_import_configures_json_logging_in_fresh_process(tmp_path):
    script = (
        "import logging\n"
        "import dotenv\n"
        # Ignore a developer's repo-root .env so only this environment applies.
        "dotenv.load_dotenv = lambda *args, **kwargs: False\n"
        "from open_webui import env\n"
        "logging.getLogger('open_webui.audit').error('AUDIT-CANARY')\n"
        "logging.getLogger('open_webui.check').warning('configured')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(BACKEND_DIR),
            "DATA_DIR": str(tmp_path),
            "ENV": "prod",
            "LOG_FORMAT": "json",
            "GLOBAL_LOG_LEVEL": "INFO",
            "OTEL_ENABLED": "false",
            "REDIS_URL": "",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    log_lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    parsed = [json.loads(line) for line in log_lines]
    assert parsed, result.stdout[-2000:]
    assert parsed[-1]["body"] == "configured"
    assert parsed[-1]["deployment.environment"] == "prod"
    assert "AUDIT-CANARY" not in result.stdout + result.stderr
