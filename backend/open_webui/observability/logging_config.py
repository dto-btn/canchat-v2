"""Central in-process logging configuration.

One root stdout handler writes JSON (``LOG_FORMAT=json``) or text
(``LOG_FORMAT=text``). When OTLP export is enabled, a second root handler sends
the same records to the logger provider. Both handlers share one filter chain:
a per-logger rate limit, log context resolution and source redaction (no
exception messages, credential-shaped values masked; see ``redaction``). Standalone entrypoints
(MCP subprocess servers, ``backend/scripts``) keep their own bootstrap: if the
root logger already has handlers this module did not install, they are kept.
"""

from __future__ import annotations

import atexit
import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping

from opentelemetry import trace
from opentelemetry.instrumentation.logging.handler import (
    LoggingHandler as _OTelLoggingHandler,
)
from opentelemetry.instrumentation.logging.handler import std_to_otel

from open_webui.observability.context import LOG_CONTEXT_VARS
from open_webui.observability.providers import initialize_providers
from open_webui.observability.redaction import (
    RedactionFilter,
    SafeHandleErrorMixin,
    exception_fields,
    format_exception_safe,
)

LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
DEFAULT_RATE_LIMIT = "100/10s"
SUMMARY_LOGGER_NAME = "open_webui.observability.logging"
AUDIT_LOGGER_NAMES = ("open_webui.audit", "open_webui.audit.content")
# Access records scale with traffic rather than with failure loops.
RATE_LIMIT_EXEMPT_LOGGERS = frozenset(
    {"uvicorn.access", "open_webui.access", SUMMARY_LOGGER_NAME}
)
# Third-party loggers pinned to WARNING. A mapped source raises its logger only
# when <SOURCE>_LOG_LEVEL is explicitly set below WARNING.
NOISY_LOGGERS: dict[str, str | None] = {
    "uvicorn.error": "MAIN",
    "sqlalchemy.engine": "DB",
    "mcp": "MCP",
    "aiohttp": None,
    "httpx": None,
    "httpcore": None,
    "litellm": None,
    "LiteLLM": None,
    "LiteLLM Proxy": None,
    "LiteLLM Router": None,
    "crewai": None,
    "peewee": None,
}
SOURCE_LOGGERS = {"open_webui.audit": "AUDIT", "mcp_backend": "MCP"}
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

RESOURCE_FIELDS = ("service.name", "service.version", "deployment.environment")
CONTEXT_FIELDS = tuple(LOG_CONTEXT_VARS)
ACCESS_FIELDS = (
    "http.request.method",
    "http.route",
    "url.path",
    "http.response.status_code",
    "http.response.body.size",
    "duration_ms",
    "client.address",
    "network.protocol.version",
)
_SEVERITY_TEXT = {"WARNING": "WARN", "CRITICAL": "FATAL"}
_RATE_LIMIT_DECISION = "_canchat_rate_limit_allowed"
_OWNED_HANDLER = "_canchat_logging_owned"
_RATE_LIMIT_PATTERN = re.compile(r"^(\d+)/(\d+(?:\.\d+)?)(s|m|h)?$")
_PERIOD_UNITS = {None: 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True)
class LogSettings:
    format: str
    rate_limit: int
    rate_period: float


def parse_rate_limit(value: str) -> tuple[int, float]:
    """Parse ``LOG_RATE_LIMIT``; ``0`` disables the limit."""
    text = value.strip().lower().replace(" ", "")
    if text == "0":
        return 0, 0.0
    match = _RATE_LIMIT_PATTERN.match(text)
    count = int(match.group(1)) if match else 0
    period = float(match.group(2)) * _PERIOD_UNITS[match.group(3)] if match else 0.0
    if count <= 0 or period <= 0:
        raise ValueError(
            "LOG_RATE_LIMIT must be '0' or '<count>/<period>' with an optional "
            "s, m or h unit, for example '100/10s'"
        )
    return count, period


def load_log_settings(environ: Mapping[str, str] | None = None) -> LogSettings:
    values = os.environ if environ is None else environ
    container = values.get("DOCKER", "false").strip().lower() == "true"
    local_dev = values.get("ENV", "dev").strip().lower() == "dev"
    default_format = "text" if local_dev and not container else "json"
    log_format = values.get("LOG_FORMAT", default_format).strip().lower()
    if log_format not in {"json", "text"}:
        raise ValueError("LOG_FORMAT must be either 'json' or 'text'")
    limit, period = parse_rate_limit(values.get("LOG_RATE_LIMIT", DEFAULT_RATE_LIMIT))
    return LogSettings(format=log_format, rate_limit=limit, rate_period=period)


def _present(value: object) -> bool:
    return value is not None and value != ""


def record_attributes(record: logging.LogRecord) -> dict[str, object]:
    """Structured fields shared by stdout JSON and OTLP export."""
    attributes: dict[str, object] = {}
    for name in (*CONTEXT_FIELDS, "event"):
        value = record.__dict__.get(name)
        if _present(value):
            attributes[name] = str(value)
    for name in ACCESS_FIELDS:
        value = record.__dict__.get(name)
        if _present(value):
            attributes[name] = value
    # Exception messages are never emitted: they can repeat prompts, URLs or
    # tool arguments. The stack trace keeps frames and exception types.
    attributes.update(exception_fields(record))
    attributes["source"] = f"{record.module}:{record.lineno}"
    return attributes


def _trace_fields() -> dict[str, str]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
        "trace_flags": format(int(span_context.trace_flags), "02x"),
    }


class JsonFormatter(logging.Formatter):
    """Formats each record as one JSON object following the log record contract."""

    def __init__(self, resource: Mapping[str, object]):
        super().__init__()
        self._resource = {
            name: str(resource[name])
            for name in RESOURCE_FIELDS
            if _present(resource.get(name))
        }

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc)
        fields: dict[str, object] = {
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "severity_text": _SEVERITY_TEXT.get(record.levelname, record.levelname),
            "severity_number": int(std_to_otel(record.levelno).value),
            "body": record.getMessage(),
            "logger": record.name,
            **self._resource,
            **_trace_fields(),
            **record_attributes(record),
        }
        return json.dumps(
            fields, ensure_ascii=False, separators=(",", ":"), default=str
        )


_TEXT_FORMATTERS: dict[str, logging.Formatter] = {}


def register_text_formatter(logger_name: str, formatter: logging.Formatter) -> None:
    """Use a specific text formatter for one logger (for example access logs)."""
    _TEXT_FORMATTERS[logger_name] = formatter


class TextFormatter(logging.Formatter):
    """The existing local-development text format, with per-logger overrides."""

    def __init__(self) -> None:
        super().__init__(logging.BASIC_FORMAT)

    def formatException(self, ei) -> str:  # noqa: N802
        return format_exception_safe(ei[1]).rstrip("\n") if ei[1] else ""

    def format(self, record: logging.LogRecord) -> str:
        formatter = _TEXT_FORMATTERS.get(record.name)
        return formatter.format(record) if formatter else super().format(record)


class LogContextFilter(logging.Filter):
    """Copies bound request/operation context onto the record at the call site."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, variable in LOG_CONTEXT_VARS.items():
            if not _present(record.__dict__.get(name)):
                value = variable.get()
                if _present(value):
                    record.__dict__[name] = value
        return True


@dataclass
class _Bucket:
    tokens: float
    updated: float
    dropped: int = 0
    first_drop: float = 0.0


class RateLimitFilter(logging.Filter):
    """Per-logger token bucket that reports dropped records in one summary line.

    The decision is stored on the record so every handler sharing this filter
    accepts or drops the same records.
    """

    def __init__(
        self,
        limit: int,
        period: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        exempt: frozenset[str] = RATE_LIMIT_EXEMPT_LOGGERS,
    ) -> None:
        super().__init__()
        self.limit = limit
        self.period = period
        self._rate = limit / period
        self._clock = clock
        self._exempt = exempt | {SUMMARY_LOGGER_NAME}
        self._buckets: dict[str, _Bucket] = {}
        self._pending: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        decision = record.__dict__.get(_RATE_LIMIT_DECISION)
        if decision is not None:
            return decision
        if record.name == SUMMARY_LOGGER_NAME:
            record.__dict__[_RATE_LIMIT_DECISION] = True
            return True

        now = self._clock()
        with self._lock:
            due = self._take_summaries(now)
            allowed = record.name in self._exempt or self._consume(record.name, now)
        record.__dict__[_RATE_LIMIT_DECISION] = allowed
        self._emit_summaries(due)
        return allowed

    def _consume(self, name: str, now: float) -> bool:
        bucket = self._buckets.get(name)
        if bucket is None:
            bucket = self._buckets[name] = _Bucket(
                tokens=float(self.limit), updated=now
            )
        else:
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(float(self.limit), bucket.tokens + elapsed * self._rate)
            bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        if bucket.dropped == 0:
            bucket.first_drop = now
            self._pending[name] = bucket
        bucket.dropped += 1
        return False

    def _take_summaries(self, now: float | None) -> list[tuple[str, int]]:
        due = []
        for name, bucket in list(self._pending.items()):
            if now is None or now - bucket.first_drop >= self.period:
                due.append((name, bucket.dropped))
                bucket.dropped = 0
                del self._pending[name]
        return due

    def _emit_summaries(self, due: list[tuple[str, int]]) -> None:
        if due:
            # Summaries are triggered by whichever record arrives next, possibly
            # from another request; an empty context keeps that request's user,
            # request and trace IDs off the summary.
            contextvars.Context().run(self._log_summaries, due)

    def _log_summaries(self, due: list[tuple[str, int]]) -> None:
        summary_logger = logging.getLogger(SUMMARY_LOGGER_NAME)
        for name, dropped in due:
            summary_logger.warning(
                "Dropped %d log records from logger %r (rate limit %d per %gs)",
                dropped,
                name,
                self.limit,
                self.period,
                extra={"event": "log.rate_limited"},
            )

    def emit_pending_summaries(self) -> None:
        """Report drops that have not been summarized yet, for example at exit."""
        with self._lock:
            due = self._take_summaries(None)
        self._emit_summaries(due)


class _ExcludeLoggersFilter(logging.Filter):
    def __init__(self, prefix: str) -> None:
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == self._prefix or record.name.startswith(self._prefix + ".")
        )


class ContractOTLPHandler(SafeHandleErrorMixin, _OTelLoggingHandler):
    """OTLP log handler that exports only the log record contract fields."""

    def _get_attributes(self, record: logging.LogRecord) -> dict[str, object]:
        return record_attributes(record)

    def emit(self, record: logging.LogRecord) -> None:
        # The SDK handler lets translation/export errors reach the caller.
        try:
            super().emit(record)
        except Exception:
            self.handleError(record)


class _StdoutHandler(SafeHandleErrorMixin, logging.StreamHandler):
    """Writes to the current ``sys.stdout``, even if it is replaced later."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


@dataclass
class _LoggingState:
    handlers: list[logging.Handler] = field(default_factory=list)
    rate_limit: RateLimitFilter | None = None
    otlp_handler: logging.Handler | None = None
    source_levels: Mapping[str, str] = field(default_factory=dict)
    environ: Mapping[str, str] = field(default_factory=dict)
    owns_root: bool = False


_lock = threading.RLock()
_state = _LoggingState()
_atexit_registered = False


def _is_owned(handler: logging.Handler) -> bool:
    return getattr(handler, _OWNED_HANDLER, False)


def _noisy_level(source: str | None, environ: Mapping[str, str]) -> str:
    explicit = environ.get(f"{source}_LOG_LEVEL", "").upper() if source else ""
    if explicit in LOG_LEVELS and logging.getLevelName(explicit) < logging.WARNING:
        return explicit
    return "WARNING"


def apply_logger_policy() -> None:
    """Apply logger levels and routing; call again after libraries add handlers."""
    with _lock:
        source_levels = _state.source_levels
        environ = _state.environ
        default_level = logging.getLevelName(logging.getLogger().level)
        for name in AUDIT_LOGGER_NAMES:
            audit_logger = logging.getLogger(name)
            audit_logger.propagate = False
            if not any(
                isinstance(h, logging.NullHandler) for h in audit_logger.handlers
            ):
                # Without a handler, Python's last-resort handler writes to stderr.
                audit_logger.addHandler(logging.NullHandler())
        for name, source in SOURCE_LOGGERS.items():
            logging.getLogger(name).setLevel(source_levels.get(source, default_level))
        for name, source in NOISY_LOGGERS.items():
            logging.getLogger(name).setLevel(_noisy_level(source, environ))
        if _state.owns_root:
            for name in (*UVICORN_LOGGERS, *NOISY_LOGGERS):
                library_logger = logging.getLogger(name)
                for handler in list(library_logger.handlers):
                    library_logger.removeHandler(handler)
                library_logger.propagate = True


def configure_logging(
    global_level: str,
    source_levels: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Install the process-wide logging configuration.

    Returns ``False`` when the entrypoint already configured the root logger;
    its handlers are kept and only the logger policy is applied.
    """
    global _atexit_registered

    values = dict(os.environ if environ is None else environ)
    settings = load_log_settings(values)
    root = logging.getLogger()
    with _lock:
        root.setLevel(global_level)
        _state.source_levels = dict(source_levels)
        _state.environ = values
        _state.owns_root = all(_is_owned(h) for h in root.handlers)
        if not _state.owns_root:
            apply_logger_policy()
            return False

        _remove_owned_handlers(root)
        providers = initialize_providers()
        rate_limit = (
            RateLimitFilter(settings.rate_limit, settings.rate_period)
            if settings.rate_limit
            else None
        )
        filter_chain = [
            f
            for f in (rate_limit, LogContextFilter(), RedactionFilter())
            if f is not None
        ]

        stdout_handler = _StdoutHandler()
        stdout_handler.setFormatter(
            JsonFormatter(providers.logger_provider.resource.attributes)
            if settings.format == "json"
            else TextFormatter()
        )
        handlers: list[logging.Handler] = [stdout_handler]
        otlp_handler = None
        if providers.export_enabled:
            otlp_handler = ContractOTLPHandler(
                logger_provider=providers.logger_provider
            )
            # The exporter's own diagnostics must not feed back into the exporter.
            otlp_handler.addFilter(_ExcludeLoggersFilter("opentelemetry"))
            handlers.append(otlp_handler)

        for handler in handlers:
            for log_filter in filter_chain:
                handler.addFilter(log_filter)
            setattr(handler, _OWNED_HANDLER, True)
            root.addHandler(handler)

        _state.handlers = handlers
        _state.rate_limit = rate_limit
        _state.otlp_handler = otlp_handler
        apply_logger_policy()
        if not _atexit_registered:
            atexit.register(shutdown_logging)
            _atexit_registered = True
    return True


def _remove_owned_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if _is_owned(handler):
            root.removeHandler(handler)
    _state.handlers = []
    _state.rate_limit = None
    _state.otlp_handler = None


def shutdown_logging() -> None:
    """Report pending rate-limit drops and detach OTLP export before providers stop."""
    with _lock:
        rate_limit = _state.rate_limit
        otlp_handler = _state.otlp_handler
        _state.otlp_handler = None
    if rate_limit is not None:
        rate_limit.emit_pending_summaries()
    if otlp_handler is not None:
        logging.getLogger().removeHandler(otlp_handler)
        if otlp_handler in _state.handlers:
            _state.handlers.remove(otlp_handler)
