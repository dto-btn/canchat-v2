"""Tracing policy and auto-instrumentation (OBS-20).

This module owns everything that decides what a span may contain and where
trace context may go:

* ``apply_semconv_opt_in`` selects the stable HTTP semantic conventions. It
  runs from ``observability/__init__.py`` before any instrumentor is imported.
* ``build_sampler`` is a parent-based trace-ID ratio sampler. Remote parents
  (client-supplied ``traceparent``) are sampled at the same ratio as new
  traces by a server-side random draw, so a client cannot force sampling. Client spans without a parent
  (health checks, heartbeats, background DB/Redis calls) are not recorded.
* ``TraceContextAllowListPropagator`` extracts W3C ``traceparent`` only.
  Baggage is never extracted or injected, incoming ``tracestate`` is
  discarded, and outbound ``traceparent`` is injected only for destinations
  in ``OTEL_PROPAGATION_ALLOWED_HOSTS``.
* ``SanitizingSpanExporter`` applies the span attribute allow-list before
  any exporter sees a span.
* ``instrument_libraries`` and ``instrument_app`` install the pinned
  FastAPI, requests, aiohttp, HTTPX, SQLAlchemy and Redis instrumentors.

Instrumentor packages are imported lazily so that importing this module
(``providers.py`` does) never initializes semantic-convention state.
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import threading
from contextvars import ContextVar
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from urllib.parse import urlsplit

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    TraceState,
)
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)
from opentelemetry.util.types import Attributes

if TYPE_CHECKING:
    import aiohttp

from open_webui.observability.context import (
    SERVER_SPAN_CONTEXT_STATE_KEY,
    request_id_var,
)

log = logging.getLogger(__name__)

SEMCONV_OPT_IN_ENV = "OTEL_SEMCONV_STABILITY_OPT_IN"
SEMCONV_OPT_IN_DEFAULT = "http"

REQUEST_ID_ATTRIBUTE = "canchat.request.id"

# Span attributes that may leave the process. Everything else is dropped at
# export. Values that can carry user data are transformed instead:
# ``url.full``/``http.url`` keep only the origin, SQL keeps no literals.
ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        # HTTP server and client (stable conventions)
        "http.request.method",
        "http.request.method_original",
        "http.request.resend_count",
        "http.response.status_code",
        "http.route",
        "url.scheme",
        "url.full",
        "url.template",
        "server.address",
        "server.port",
        "network.protocol.name",
        "network.protocol.version",
        "error.type",
        # Database (SQLAlchemy and Redis, old and stable conventions)
        "db.system",
        "db.system.name",
        "db.name",
        "db.namespace",
        "db.operation",
        "db.operation.name",
        "db.collection.name",
        "db.statement",
        "db.query.text",
        "db.redis.database_index",
        "db.redis.args_length",
        "net.peer.name",
        "net.peer.port",
        "net.transport",
        # Pre-stable HTTP names, in case an instrumentor ignores the opt-in
        "http.method",
        "http.status_code",
        "http.url",
        "http.scheme",
        # GenAI metadata on ``llm.chat`` (OBS-25; ``llm.GEN_AI_ATTRIBUTES``).
        # Message, system-instruction and tool content keys are never allowed.
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    }
)
ALLOWED_SPAN_ATTRIBUTE_PREFIXES = ("canchat.",)
# Exception events keep the type and whether it escaped the span. Messages and stack traces can repeat
# prompts, URLs or credentials (see OBS-18).
ALLOWED_EVENT_ATTRIBUTES = frozenset({"exception.type", "exception.escaped"})

_URL_ATTRIBUTES = frozenset({"url.full", "http.url"})
_SQL_ATTRIBUTES = frozenset({"db.statement", "db.query.text"})
# On server spans these come from the client-supplied Host header.
_SERVER_SPAN_DROPPED_ATTRIBUTES = frozenset({"server.address", "server.port"})
_MAX_STATEMENT_LENGTH = 2048
# Backslash escapes are treated as escapes even where the dialect would not;
# over-redacting a statement is the safe failure.
_SQL_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*(?:'|$)", re.DOTALL)
_SQL_DOLLAR_LITERAL = re.compile(r"\$(\w*)\$.*?(?:\$\1\$|$)", re.DOTALL)
_SQL_HEX_LITERAL = re.compile(r"\b0x[0-9a-fA-F]+\b")
_SQL_NUMERIC_LITERAL = re.compile(r"(?<![\w$.:])\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b")

# ``excluded_urls`` regexes are matched against ``scheme://host/path`` (no
# query). Socket.IO gets per-event context in OBS-27; a server span per
# websocket connection would turn every socket event into one long trace.
_WEBSOCKET_URL_PATTERN = r"^wss?://"
_SOCKETIO_URL_PATTERN = r"^https?://[^/]*/ws(?:/|$)"
_HEALTH_URL_PATTERN = r"^https?://[^/]*/health"


def apply_semconv_opt_in(environ: MutableMapping[str, str] | None = None) -> str:
    """Select the stable HTTP conventions before instrumentors are loaded."""
    values = os.environ if environ is None else environ
    value = values.setdefault(SEMCONV_OPT_IN_ENV, SEMCONV_OPT_IN_DEFAULT)
    tokens = {token.strip() for token in value.split(",")}
    # ``http/dup`` would emit the old and the stable metric families together.
    if "http" not in tokens or "http/dup" in tokens:
        raise ValueError(
            f"{SEMCONV_OPT_IN_ENV} must include 'http' and must not include 'http/dup'"
        )
    return value


def _require_stable_http_semconv() -> None:
    from opentelemetry.instrumentation._semconv import (
        _OpenTelemetrySemanticConventionStability,
        _OpenTelemetryStabilitySignalType,
        _StabilityMode,
    )

    apply_semconv_opt_in()
    _OpenTelemetrySemanticConventionStability._initialize()
    mode = _OpenTelemetrySemanticConventionStability._get_opentelemetry_stability_opt_in_mode(
        _OpenTelemetryStabilitySignalType.HTTP
    )
    if mode is not _StabilityMode.HTTP:
        raise RuntimeError(
            "OpenTelemetry semantic conventions were initialized before "
            f"{SEMCONV_OPT_IN_ENV}=http was applied"
        )


########################################
# Sampling
########################################


class _RootSampler(Sampler):
    """Samples new traces by ratio, but never starts a trace at a client span."""

    def __init__(self, ratio_sampler: TraceIdRatioBased):
        self._ratio_sampler = ratio_sampler

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        if kind is SpanKind.CLIENT:
            return SamplingResult(Decision.DROP)
        return self._ratio_sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"CANChatRoot{{noOrphanClient,{self._ratio_sampler.get_description()}}}"


def _remote_sampling_draw() -> float:
    # Sampling only: the client must not choose the outcome, which does not
    # need cryptographic strength.
    return random.random()  # nosec B311


class _RemoteParentSampler(Sampler):
    """Samples client-supplied parents by a server-side random draw.

    ``TraceIdRatioBased`` decides from the trace ID, which a client chooses
    in its ``traceparent``; a low trace ID would be sampled at any ratio.
    """

    def __init__(self, ratio: float):
        self._ratio = ratio

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        decision = (
            Decision.RECORD_AND_SAMPLE
            if _remote_sampling_draw() < self._ratio
            else Decision.DROP
        )
        parent_state = (
            trace.get_current_span(parent_context).get_span_context().trace_state
        )
        return SamplingResult(decision, None, parent_state)

    def get_description(self) -> str:
        return f"CANChatRemoteParent{{random,ratio={self._ratio}}}"


def build_sampler(ratio: float) -> Sampler:
    """Parent-based sampler where remote sampling flags and IDs are ignored.

    Local parents keep their decision, so a trace is never partially sampled
    inside the process. Remote parents are sampled at the same ratio as new
    traces, by a server-side random draw: a client sending ``-01`` or a
    hand-picked trace ID cannot raise the sampling rate, and a client sending
    ``-00`` cannot opt out.
    """
    remote_sampler = _RemoteParentSampler(ratio)
    return ParentBased(
        root=_RootSampler(TraceIdRatioBased(ratio)),
        remote_parent_sampled=remote_sampler,
        remote_parent_not_sampled=remote_sampler,
    )


########################################
# Propagation
########################################

_propagation_lock = threading.Lock()
_propagation_allowed_hosts: tuple[str, ...] = ()
# (trace_id, span_id) of the client span whose destination was approved by
# a request hook. The hooks run immediately before ``inject`` in the same
# context, so ``inject`` can check that the current span is that span.
_approved_client_span: ContextVar[tuple[int, int] | None] = ContextVar(
    "otel_approved_client_span", default=None
)


def host_allowed(host: str | None, allowed_hosts: Sequence[str]) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    for entry in allowed_hosts:
        if entry.startswith("*."):
            if host.endswith(entry[1:]):
                return True
        elif host == entry:
            return True
    return False


def _url_host(url: Any) -> str | None:
    try:
        return urlsplit(str(url)).hostname
    except ValueError:
        return None


def approve_propagation(span: trace.Span, url: Any) -> bool:
    """Allow ``traceparent`` on the request ``span`` makes if ``url`` is approved."""
    span_context = span.get_span_context()
    allowed = span_context.is_valid and host_allowed(
        _url_host(url), _propagation_allowed_hosts
    )
    _approved_client_span.set(
        (span_context.trace_id, span_context.span_id) if allowed else None
    )
    return allowed


class TraceContextAllowListPropagator(TextMapPropagator):
    """W3C trace context only, with allow-listed outbound injection."""

    def __init__(self) -> None:
        self._delegate = TraceContextTextMapPropagator()

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        extracted = self._delegate.extract(carrier, context, getter)
        span_context = trace.get_current_span(extracted).get_span_context()
        if not (span_context.is_valid and span_context.is_remote):
            return extracted
        if not len(span_context.trace_state):
            return extracted
        # Incoming tracestate is client-controlled vendor data; never forward it.
        return trace.set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=span_context.trace_id,
                    span_id=span_context.span_id,
                    is_remote=True,
                    trace_flags=span_context.trace_flags,
                    trace_state=TraceState(),
                )
            ),
            extracted,
        )

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        # Redirects reuse the previous hop's headers, so a header injected for
        # an approved host must not survive to a hop that is not approved.
        _remove_fields(carrier, self.fields)
        span_context = trace.get_current_span(context).get_span_context()
        if not span_context.is_valid:
            return
        if _approved_client_span.get() != (
            span_context.trace_id,
            span_context.span_id,
        ):
            return
        self._delegate.inject(carrier, context, setter)

    @property
    def fields(self) -> set[str]:
        return self._delegate.fields


def _remove_fields(carrier: Any, fields: AbstractSet[str]) -> None:
    try:
        keys = [key for key in carrier.keys() if str(key).lower() in fields]
        for key in keys:
            del carrier[key]
    except (AttributeError, KeyError, TypeError):
        log.warning(
            "Could not remove trace headers from %s; redirected requests may keep them",
            type(carrier).__name__,
        )


def configure_propagation(allowed_hosts: Sequence[str]) -> None:
    """Install the process-wide propagator. ``OTEL_PROPAGATORS`` is ignored."""
    global _propagation_allowed_hosts

    with _propagation_lock:
        _propagation_allowed_hosts = tuple(allowed_hosts)
        propagate.set_global_textmap(TraceContextAllowListPropagator())


def _requests_hook(span: trace.Span, request: Any) -> None:
    approve_propagation(span, getattr(request, "url", None))


def _aiohttp_hook(span: trace.Span, params: Any) -> None:
    approve_propagation(span, getattr(params, "url", None))


def _httpx_hook(span: trace.Span, request_info: Any) -> None:
    approve_propagation(span, getattr(request_info, "url", None))


async def _httpx_async_hook(span: trace.Span, request_info: Any) -> None:
    approve_propagation(span, getattr(request_info, "url", None))


# HTTPX starts a client span and calls ``inject`` for every redirect hop, so
# the propagator handles it. requests follows redirects with instrumentation
# suppressed and aiohttp reuses the first hop's span and headers, so neither
# calls ``inject`` again; redirected hops get no trace context at all.
_TRACE_HEADERS = frozenset({"traceparent", "tracestate"})


def _requests_rebuild_auth(
    wrapped: Callable[..., Any],
    _instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    result = wrapped(*args, **kwargs)
    prepared_request = args[0] if args else kwargs.get("prepared_request")
    if prepared_request is not None:
        _remove_fields(prepared_request.headers, _TRACE_HEADERS)
    return result


def _aiohttp_redirect_trace_config() -> aiohttp.TraceConfig:
    import aiohttp

    async def on_request_redirect(
        _session: aiohttp.ClientSession,
        _ctx: Any,
        params: aiohttp.TraceRequestRedirectParams,
    ) -> None:
        _remove_fields(params.headers, _TRACE_HEADERS)

    trace_config = aiohttp.TraceConfig()
    trace_config.on_request_redirect.append(on_request_redirect)
    return trace_config


def _wrap_requests_redirects() -> None:
    import requests
    import wrapt

    wrapt.wrap_function_wrapper(
        requests.Session, "rebuild_auth", _requests_rebuild_auth
    )


def _unwrap_requests_redirects() -> None:
    import requests
    from opentelemetry.instrumentation.utils import unwrap

    unwrap(requests.Session, "rebuild_auth")


########################################
# Span attribute allow-list
########################################


def url_origin(value: Any) -> str | None:
    """Keep ``scheme://host[:port]``; drop credentials, path, query and fragment."""
    try:
        parts = urlsplit(str(value))
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not hostname:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def sanitize_sql(statement: Any) -> str:
    """Replace SQL string and numeric literals with ``?`` and bound the length."""
    sanitized = _SQL_DOLLAR_LITERAL.sub("?", str(statement))
    sanitized = _SQL_STRING_LITERAL.sub("?", sanitized)
    sanitized = _SQL_HEX_LITERAL.sub("?", sanitized)
    sanitized = _SQL_NUMERIC_LITERAL.sub("?", sanitized)
    if len(sanitized) > _MAX_STATEMENT_LENGTH:
        sanitized = sanitized[: _MAX_STATEMENT_LENGTH - 3] + "..."
    return sanitized


def _attribute_allowed(key: str) -> bool:
    return key in ALLOWED_SPAN_ATTRIBUTES or key.startswith(
        ALLOWED_SPAN_ATTRIBUTE_PREFIXES
    )


def filter_span_attributes(attributes: Attributes) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if not _attribute_allowed(key):
            continue
        if key in _URL_ATTRIBUTES:
            value = url_origin(value)
            if value is None:
                continue
        elif key in _SQL_ATTRIBUTES:
            value = sanitize_sql(value)
        filtered[key] = value
    return filtered


def _filter_event_attributes(attributes: Attributes) -> dict[str, Any]:
    event_attributes = {
        key: value
        for key, value in (attributes or {}).items()
        if key in ALLOWED_EVENT_ATTRIBUTES
    }
    event_attributes.update(filter_span_attributes(attributes))
    return event_attributes


def sanitize_span(span: ReadableSpan) -> ReadableSpan:
    """Return a copy of ``span`` that only holds allow-listed data."""
    attributes = filter_span_attributes(span.attributes)
    if span.kind is SpanKind.SERVER:
        for key in _SERVER_SPAN_DROPPED_ATTRIBUTES:
            attributes.pop(key, None)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=tuple(
            Event(
                event.name,
                _filter_event_attributes(event.attributes),
                event.timestamp,
            )
            for event in span.events
        ),
        links=tuple(
            Link(link.context, filter_span_attributes(link.attributes))
            for link in span.links
        ),
        kind=span.kind,
        # Status descriptions repeat exception messages; keep the code only.
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class SanitizingSpanExporter(SpanExporter):
    """Applies the span allow-list before delegating to a real exporter."""

    def __init__(self, delegate: SpanExporter):
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._delegate.export([sanitize_span(span) for span in spans])

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


########################################
# Instrumentation
########################################

_instrumentation_lock = threading.Lock()
_libraries_instrumented = False


def _bundle():
    from open_webui.observability.providers import initialize_providers

    return initialize_providers()


def server_excluded_urls(include_healthchecks: bool) -> str:
    patterns = [_WEBSOCKET_URL_PATTERN, _SOCKETIO_URL_PATTERN]
    if not include_healthchecks:
        patterns.append(_HEALTH_URL_PATTERN)
    return ",".join(patterns)


def _server_request_hook(span: trace.Span, scope: Mapping[str, Any]) -> None:
    span_context = span.get_span_context()
    state = scope.get("state")
    if span_context.is_valid and isinstance(state, dict):
        state[SERVER_SPAN_CONTEXT_STATE_KEY] = span_context
    request_id = request_id_var.get()
    if request_id and span.is_recording():
        # Validated by RequestContextMiddleware: ^[A-Za-z0-9._-]{8,64}$ or a uuid4.
        span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)


def _instruments() -> tuple[tuple[Any, dict[str, Any]], ...]:
    from opentelemetry.instrumentation.aiohttp_client import (
        AioHttpClientInstrumentor,
    )
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    return (
        (RequestsInstrumentor(), {"request_hook": _requests_hook}),
        (
            AioHttpClientInstrumentor(),
            {
                "request_hook": _aiohttp_hook,
                "trace_configs": [_aiohttp_redirect_trace_config()],
            },
        ),
        (
            HTTPXClientInstrumentor(),
            {
                "request_hook": _httpx_hook,
                "async_request_hook": _httpx_async_hook,
            },
        ),
        (RedisInstrumentor(), {}),
        # The commenter would append trace data to executed SQL; it stays off.
        (SQLAlchemyInstrumentor(), {"enable_commenter": False}),
    )


def instrument_libraries(
    *,
    tracer_provider: trace.TracerProvider | None = None,
    meter_provider: Any = None,
    force: bool = False,
) -> bool:
    """Instrument outbound HTTP, SQLAlchemy and Redis clients once per process.

    Call before ``open_webui.internal.db`` is imported: SQLAlchemy engines are
    traced when they are created. Does nothing when ``OTEL_ENABLED=false``
    unless ``force`` is set (tests pass their own providers).
    """
    global _libraries_instrumented

    with _instrumentation_lock:
        if _libraries_instrumented:
            return True
        if tracer_provider is None or meter_provider is None:
            bundle = _bundle()
            if not bundle.export_enabled and not force:
                return False
            tracer_provider = tracer_provider or bundle.tracer_provider
            meter_provider = meter_provider or bundle.meter_provider

        _require_stable_http_semconv()
        if "open_webui.internal.db" in sys.modules:
            log.warning(
                "SQLAlchemy was instrumented after open_webui.internal.db was "
                "imported; database queries will not be traced"
            )
        for instrumentor, options in _instruments():
            instrumentor.instrument(
                tracer_provider=tracer_provider,
                meter_provider=meter_provider,
                **options,
            )
        _wrap_requests_redirects()
        _libraries_instrumented = True
        return True


def uninstrument_libraries() -> None:
    global _libraries_instrumented

    with _instrumentation_lock:
        if not _libraries_instrumented:
            return
        for instrumentor, _ in _instruments():
            instrumentor.uninstrument()
        _unwrap_requests_redirects()
        _libraries_instrumented = False


def instrument_app(
    app: Any,
    *,
    tracer_provider: trace.TracerProvider | None = None,
    meter_provider: Any = None,
    include_healthchecks: bool | None = None,
    force: bool = False,
) -> bool:
    """Add server spans and ``http.server.request.duration`` to a FastAPI app.

    Spans are named from route templates (``GET /api/v1/chats/{id}``), so
    span names and metric labels stay bounded. Per-chunk ASGI send/receive
    spans are disabled.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if (
        tracer_provider is None
        or meter_provider is None
        or include_healthchecks is None
    ):
        bundle = _bundle()
        if not bundle.export_enabled and not force:
            return False
        tracer_provider = tracer_provider or bundle.tracer_provider
        meter_provider = meter_provider or bundle.meter_provider
        if include_healthchecks is None:
            include_healthchecks = bool(
                bundle.settings and bundle.settings.traces_healthchecks
            )

    _require_stable_http_semconv()
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls=server_excluded_urls(include_healthchecks),
        exclude_spans=["receive", "send"],
        server_request_hook=_server_request_hook,
    )
    return True
