"""Source-confidentiality canary harness (OBS-17).

Tests use this harness to show that synthetic secrets put into a request do
not reach any operational sink. An empty ``caplog`` does not prove that: a
value can still reach stdout through ``print``, stderr through a child process
or ``logging.Handler.handleError``, a non-propagating logger, an OTLP log
export, or a span attribute or exception event.

Usage::

    def test_handler_keeps_prompt_out_of_logs(canary_harness):
        canaries = canary_harness.canaries
        run_handler(prompt=canaries.prompt, token=canaries.token)
        canary_harness.assert_no_canaries()

The ``canary_harness`` fixture (see ``open_webui/test/conftest.py``) builds a
fresh :class:`Canaries` set for each test and starts capturing at once.

What is captured while the harness is active:

``stdout`` / ``stderr``
    Taken from pytest's ``capfd``, so this includes file-descriptor writes from
    C extensions and child processes that inherit the descriptors (for example
    MCP subprocess stderr). Tests that use the harness must not also request
    ``capsys``.
``log records``
    Every record that reaches the root logger, and any non-propagating logger
    that exists when the harness starts. Records are checked in full: message,
    format arguments, extra fields, the whole exception chain and stack info.
    This is deliberately stricter than the current formatters, because any
    handler or later formatter change could emit those fields. The restricted
    ``open_webui.audit*`` loggers are excluded; they are not operational sinks.
``json handler``
    The records formatted by the production :class:`JsonFormatter` with the
    production :class:`LogContextFilter`, exactly as the stdout handler would
    write them with ``LOG_FORMAT=json``.
``otlp logs``
    Records exported through the production :class:`ContractOTLPHandler` to an
    in-memory exporter, plus anything else emitted through the process-wide
    OTel logger provider.

Source filters (OBS-18): like the production stdout and OTLP handlers, every
handler the harness installs first runs the production :class:`RedactionFilter`.
It redacts the record in place, so the ``log records`` sink holds exactly what
any production handler receives: no exception messages and masked
credential-shaped values. Extra fields are still checked in full. Pass
``source_filters=False`` to see raw records (the harness self-tests do this).
``spans``
    Every span ended on the process-wide OTel tracer provider (name,
    attributes, events including exception events, status and resource),
    read from an in-memory exporter.

Logger policy: the root logger and every application logger are lowered to
DEBUG, so DEBUG-only code paths run. As in production, third-party loggers in
``logging_config.NOISY_LOGGERS`` stay pinned to WARNING and the
``open_webui.audit*`` loggers do not propagate. Call
:meth:`CanaryHarness.set_level` to opt a logger in to DEBUG.

Matching: each canary and its fragments (for example the first and last
12 characters of a token) are searched case-insensitively. The search also
covers URL-encoded and JSON-escaped forms. Add test-specific values with
``canaries.add(label, value)``.

Guard against tests that pass without checking anything:
:meth:`CanaryHarness.assert_no_canaries` fails if no log record was captured,
unless ``require_records=False``. If a test never calls it, the fixture runs
it at teardown.
"""

from __future__ import annotations

import io
import json
import logging
import secrets
import threading
import traceback
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote, quote_plus

from opentelemetry import trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from open_webui.observability.logging_config import (
    AUDIT_LOGGER_NAMES,
    NOISY_LOGGERS,
    ContractOTLPHandler,
    JsonFormatter,
    LogContextFilter,
)
from open_webui.observability.providers import initialize_providers
from open_webui.observability.redaction import RedactionFilter

FRAGMENT_LENGTH = 12
SINK_NAMES = (
    "stdout",
    "stderr",
    "log records",
    "json handler",
    "otlp logs",
    "spans",
)


@dataclass(frozen=True)
class Canary:
    label: str
    value: str
    fragments: tuple[str, ...] = ()

    def needles(self) -> list[str]:
        """Lowercased search strings: value, fragments and their encodings."""
        found: list[str] = []
        for text in (self.value, *self.fragments):
            for variant in (
                text,
                quote(text, safe=""),
                quote_plus(text, safe=""),
                json.dumps(text, ensure_ascii=True)[1:-1],
            ):
                needle = variant.lower()
                if needle and needle not in found:
                    found.append(needle)
        return found


def _edge_fragments(value: str) -> tuple[str, ...]:
    if len(value) < FRAGMENT_LENGTH * 2:
        return ()
    return (value[:FRAGMENT_LENGTH], value[-FRAGMENT_LENGTH:])


class Canaries:
    """Synthetic sensitive values, unique to one harness instance.

    Every value contains a random tag, so a match cannot come from another
    test or from ordinary text.
    """

    def __init__(self, tag: str | None = None) -> None:
        self.tag = tag or secrets.token_hex(5)
        tag = self.tag
        self.prompt = f"{tag} canary prompt: summarize the restricted file {tag}"
        self.response = f"{tag} canary response: the restricted answer is {tag}"
        self.email = f"canary.{tag}@example.invalid"
        self.name = f"Canary Person {tag}"
        self.api_key = f"sk-{tag}{secrets.token_hex(16)}"
        self.token = (
            f"eyJ{tag}{secrets.token_urlsafe(9)}"
            f".eyJ{secrets.token_urlsafe(24)}"
            f".{secrets.token_urlsafe(24)}{tag}"
        )
        self.password = f"pw-{tag}-{secrets.token_hex(6)}"
        self._items: dict[str, Canary] = {}
        self.add("prompt", self.prompt, (self.prompt[:20],))
        self.add("response", self.response, (self.response[:20],))
        self.add("email", self.email, (self.email.split("@")[0],))
        self.add("name", self.name)
        self.add("api key", self.api_key, _edge_fragments(self.api_key))
        self.add(
            "token",
            self.token,
            (*_edge_fragments(self.token), *self.token.split(".")),
        )
        self.add("password", self.password)

    def add(self, label: str, value: str, fragments: Iterable[str] = ()) -> str:
        """Watch an extra test-specific value; returns ``value`` for chaining."""
        if not value:
            raise ValueError("canary value must not be empty")
        self._items[label] = Canary(label, value, tuple(f for f in fragments if f))
        return value

    def __iter__(self):
        return iter(self._items.values())

    def __getitem__(self, label: str) -> Canary:
        return self._items[label]


@dataclass(frozen=True)
class Leak:
    canary: str
    sink: str
    needle: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.canary} found in {self.sink} (matched {self.needle!r}): …{self.excerpt}…"


class CanaryLeakError(AssertionError):
    def __init__(self, leaks: list[Leak]):
        self.leaks = leaks
        lines = "\n".join(f"  - {leak}" for leak in leaks)
        super().__init__(f"{len(leaks)} canary leak(s) detected:\n{lines}")


def _exception_chain(exc: BaseException | None) -> list[BaseException]:
    chain: list[BaseException] = []
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


def snapshot_record(record: logging.LogRecord) -> str:
    """Serialize everything a handler could emit for ``record``."""
    parts = [record.name, record.levelname, repr(record.msg), repr(record.args)]
    try:
        parts.append(record.getMessage())
    except Exception as error:  # a broken format string is still a record
        parts.append(f"<getMessage failed: {type(error).__name__}>")
    parts.append(
        repr(
            {
                key: value
                for key, value in vars(record).items()
                if key not in {"msg", "args", "exc_info"}
            }
        )
    )
    if record.exc_info and record.exc_info[1] is not None:
        parts.append("".join(traceback.format_exception(*record.exc_info)))
        parts.extend(repr(exc.args) for exc in _exception_chain(record.exc_info[1]))
    return "\n".join(parts)


class _RecordCaptureHandler(logging.Handler):
    """Snapshots records at emit time; later mutation cannot hide a leak."""

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []
        self.snapshots: list[str] = []
        self._seen: set[int] = set()

    def emit(self, record: logging.LogRecord) -> None:
        # A record can reach this handler from both a non-propagating logger
        # and the root; keep one copy.
        if id(record) in self._seen:
            return
        self._seen.add(id(record))
        self.records.append(record)
        self.snapshots.append(snapshot_record(record))


class _ForwardingProcessor:
    """Registered once per global provider; forwards only while a harness is active.

    SDK providers cannot remove processors, so the harness switches targets
    instead of adding a processor per test.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._targets: list = []

    def attach(self, target) -> None:
        with self._lock:
            self._targets.append(target)

    def detach(self, target) -> None:
        with self._lock:
            if target in self._targets:
                self._targets.remove(target)

    def _current(self) -> list:
        with self._lock:
            return list(self._targets)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _ForwardingSpanProcessor(_ForwardingProcessor, SpanProcessor):
    def on_start(self, span, parent_context=None) -> None:
        pass

    def on_end(self, span) -> None:
        for target in self._current():
            target.on_end(span)


class _ForwardingLogProcessor(_ForwardingProcessor, LogRecordProcessor):
    def on_emit(self, log_record) -> None:
        for target in self._current():
            target.on_emit(log_record)


_FORWARDER_ATTRIBUTE = "_canchat_canary_forwarder"


def _forwarder(provider, factory, add):
    forwarder = getattr(provider, _FORWARDER_ATTRIBUTE, None)
    if forwarder is None:
        forwarder = factory()
        add(forwarder)
        setattr(provider, _FORWARDER_ATTRIBUTE, forwarder)
    return forwarder


def _sdk_provider(provider, kind: type, signal: str):
    if not isinstance(provider, kind):
        raise RuntimeError(
            f"The global OTel {signal} provider is {type(provider).__name__}, "
            f"not the SDK provider; the canary harness cannot see {signal} output."
        )
    if getattr(provider, "_disabled", False):
        raise RuntimeError(
            f"OTEL_SDK_DISABLED is set, so {signal} are not recorded; unset it "
            "to run canary tests."
        )
    return provider


class CanaryHarness:
    """Captures every operational sink and checks it for canary values."""

    def __init__(
        self,
        capfd,
        canaries: Canaries | None = None,
        *,
        source_filters: bool = True,
    ) -> None:
        self.canaries = canaries or Canaries()
        self._source_filters = source_filters
        self._capfd = capfd
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._records = _RecordCaptureHandler()
        self._json_stream = io.StringIO()
        self._span_exporter = InMemorySpanExporter()
        self._log_exporter = InMemoryLogRecordExporter()
        self._span_target = SimpleSpanProcessor(self._span_exporter)
        self._log_target = SimpleLogRecordProcessor(self._log_exporter)
        self._otlp_provider: LoggerProvider | None = None
        self._installed: list[tuple[logging.Logger, logging.Handler]] = []
        self._saved_levels: dict[logging.Logger, int] = {}
        self._saved_propagate: dict[logging.Logger, bool] = {}
        self._saved_disable = logging.NOTSET
        self._span_forwarder: _ForwardingSpanProcessor | None = None
        self._log_forwarder: _ForwardingLogProcessor | None = None
        self.checked = False

    # ── lifecycle ──────────────────────────────────────────────────────────

    def __enter__(self) -> "CanaryHarness":
        initialize_providers()
        self._attach_otel()
        self._capfd.readouterr()
        self._raise_levels()
        self._install_handlers()
        return self

    def __exit__(self, *exc_info) -> None:
        self._read_fds()
        for logger, handler in self._installed:
            logger.removeHandler(handler)
        self._installed.clear()
        for logger, level in self._saved_levels.items():
            logger.setLevel(level)
        self._saved_levels.clear()
        for logger, propagate in self._saved_propagate.items():
            logger.propagate = propagate
        self._saved_propagate.clear()
        logging.disable(self._saved_disable)
        if self._span_forwarder is not None:
            self._span_forwarder.detach(self._span_target)
        if self._log_forwarder is not None:
            self._log_forwarder.detach(self._log_target)
        if self._otlp_provider is not None:
            self._otlp_provider.shutdown()

    def _attach_otel(self) -> None:
        tracer_provider = _sdk_provider(
            trace.get_tracer_provider(), TracerProvider, "spans"
        )
        self._span_forwarder = _forwarder(
            tracer_provider,
            _ForwardingSpanProcessor,
            tracer_provider.add_span_processor,
        )
        self._span_forwarder.attach(self._span_target)

        logger_provider = _sdk_provider(get_logger_provider(), LoggerProvider, "logs")
        self._log_forwarder = _forwarder(
            logger_provider,
            _ForwardingLogProcessor,
            logger_provider.add_log_record_processor,
        )
        self._log_forwarder.attach(self._log_target)

        # A private provider, so the harness never sends records to a real
        # OTLP endpoint even if export is enabled in the test environment.
        self._otlp_provider = LoggerProvider(
            resource=logger_provider.resource, shutdown_on_exit=False
        )
        self._otlp_provider.add_log_record_processor(self._log_target)

    def _raise_levels(self) -> None:
        self._saved_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        # Production logger policy: audit records never reach root handlers.
        for name in AUDIT_LOGGER_NAMES:
            logger = logging.getLogger(name)
            self._saved_propagate.setdefault(logger, logger.propagate)
            logger.propagate = False
            if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
                self._add_handler(logger, logging.NullHandler())
        self.set_level(logging.getLogger(), logging.DEBUG)
        for name, logger in list(logging.root.manager.loggerDict.items()):
            if not isinstance(logger, logging.Logger):
                continue
            if name in NOISY_LOGGERS:
                self.set_level(logger, logging.WARNING)
            elif logger.level > logging.DEBUG:
                self.set_level(logger, logging.DEBUG)

    def _install_handlers(self) -> None:
        json_handler = logging.StreamHandler(self._json_stream)
        json_handler.setFormatter(
            JsonFormatter(self._otlp_provider.resource.attributes)
        )
        json_handler.addFilter(LogContextFilter())
        otlp_handler = ContractOTLPHandler(logger_provider=self._otlp_provider)
        otlp_handler.addFilter(LogContextFilter())
        if self._source_filters:
            # One shared instance, as in production; it redacts in place.
            redaction = RedactionFilter()
            for handler in (self._records, json_handler, otlp_handler):
                handler.addFilter(redaction)

        root = logging.getLogger()
        # First in line, so they see each record before any handler that the
        # process logging configuration installed earlier.
        for index, handler in enumerate((self._records, json_handler, otlp_handler)):
            self._add_handler(root, handler, index=index)
        for name, logger in list(logging.root.manager.loggerDict.items()):
            if (
                isinstance(logger, logging.Logger)
                and not logger.propagate
                and not _is_audit_logger(name)
            ):
                self._add_handler(logger, self._records)

    def _add_handler(
        self,
        logger: logging.Logger,
        handler: logging.Handler,
        index: int | None = None,
    ) -> None:
        if index is None:
            logger.addHandler(handler)
        elif handler not in logger.handlers:
            logger.handlers.insert(index, handler)
        self._installed.append((logger, handler))

    # ── public API ─────────────────────────────────────────────────────────

    def set_level(self, logger: logging.Logger | str, level: int) -> None:
        """Change a logger level for this test only."""
        if isinstance(logger, str):
            logger = logging.getLogger(logger)
        self._saved_levels.setdefault(logger, logger.level)
        logger.setLevel(level)

    @property
    def records(self) -> list[logging.LogRecord]:
        return list(self._records.records)

    def _read_fds(self) -> None:
        captured = self._capfd.readouterr()
        self._stdout.append(captured.out)
        self._stderr.append(captured.err)

    def sinks(self) -> dict[str, str]:
        """Current text of every sink, keyed by sink name."""
        self._read_fds()
        for handler in logging.getLogger().handlers:
            handler.flush()
        return {
            "stdout": "".join(self._stdout),
            "stderr": "".join(self._stderr),
            "log records": "\n".join(self._records.snapshots),
            "json handler": self._json_stream.getvalue(),
            "otlp logs": "\n".join(
                item.to_json() for item in self._log_exporter.get_finished_logs()
            ),
            "spans": "\n".join(
                span.to_json() for span in self._span_exporter.get_finished_spans()
            ),
        }

    def find_leaks(self) -> list[Leak]:
        leaks: list[Leak] = []
        for sink, text in self.sinks().items():
            haystack = text.lower()
            for canary in self.canaries:
                for needle in canary.needles():
                    index = haystack.find(needle)
                    if index >= 0:
                        start = max(0, index - 60)
                        excerpt = text[start : index + len(needle) + 60]
                        leaks.append(
                            Leak(canary.label, sink, needle, excerpt.replace("\n", "⏎"))
                        )
                        break
        return leaks

    def assert_no_canaries(self, *, require_records: bool = True) -> None:
        """Fail if any canary or fragment appears in any captured sink."""
        self.checked = True
        leaks = self.find_leaks()
        if leaks:
            raise CanaryLeakError(leaks)
        if require_records and not self._records.records:
            raise AssertionError(
                "The canary harness captured no log records; the test is not "
                "exercising the code path, or logging is disabled."
            )


def _is_audit_logger(name: str) -> bool:
    return any(
        name == audit or name.startswith(audit + ".") for audit in AUDIT_LOGGER_NAMES
    )
