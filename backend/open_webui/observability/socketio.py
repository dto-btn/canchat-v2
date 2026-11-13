"""Per-event context for Socket.IO handlers (OBS-27).

Every handled event runs inside ``observe_event``, which:

* resolves the actor from the server-side connection registry, populated on
  ``connect``/``user-join`` from a decoded token. Nothing the client puts in
  the payload is trusted as identity;
* reads the optional browser carrier ``data["carrier"]`` =
  ``{"operation_id": ..., "traceparent": ...}``. The operation ID follows the
  ``X-Request-Id`` rules and is never the socket ID; an invalid value is
  ignored and a server ID is generated. An invalid ``traceparent`` is ignored
  and the event starts a new trace. Remote sampling flags are not trusted
  (see ``tracing.build_sampler``);
* binds ``request_id``/``operation_id``/``user_id`` for logs, and for business
  events opens one ``socketio.event`` span, then resets all of it when the
  handler returns, so nothing leaks into the next event on the same socket;
* counts the event once in ``canchat.socketio.events`` by ``event`` (the
  fixed handler name) and ``outcome``.

``canchat.socketio.connections`` reports the sockets connected to this
instance; a reconnect is a new socket ID, so the registry is keyed by socket
ID and removal is idempotent.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterator, Mapping

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from open_webui.metrics.service import get_metrics_service
from open_webui.observability.context import log_context
from open_webui.observability.middleware import REQUEST_ID_PATTERN
from open_webui.observability.tracing import (
    REQUEST_ID_ATTRIBUTE,
    TraceContextAllowListPropagator,
)

TRACER_NAME = "open_webui.socketio"
SPAN_NAME = "socketio.event"
SOCKETIO_EVENTS = "canchat.socketio.events"
SOCKETIO_CONNECTIONS = "canchat.socketio.connections"

CARRIER_KEY = "carrier"
CARRIER_OPERATION_ID = "operation_id"
CARRIER_TRACEPARENT = "traceparent"
# Version 00 is exactly 55 characters; later versions may append fields.
_MAX_TRACEPARENT_LENGTH = 256

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_REJECTED = "rejected"
OUTCOME_CANCELLED = "cancelled"
OUTCOMES = frozenset(
    {OUTCOME_SUCCESS, OUTCOME_ERROR, OUTCOME_REJECTED, OUTCOME_CANCELLED}
)

_propagator = TraceContextAllowListPropagator()


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


########################################
# Connection registry
########################################

_connections_lock = threading.Lock()
# sid -> authenticated user ID (``None`` until the socket authenticates).
_connections: dict[str, str | None] = {}


def register_connection(sid: str, user_id: str | None = None) -> None:
    with _connections_lock:
        _connections[sid] = user_id


def bind_connection_user(sid: str, user_id: str | None) -> None:
    """Record the user a socket authenticated as (``user-join`` after reconnect)."""
    with _connections_lock:
        _connections[sid] = user_id


def unregister_connection(sid: str) -> None:
    with _connections_lock:
        _connections.pop(sid, None)


def connection_user_id(sid: str) -> str | None:
    with _connections_lock:
        return _connections.get(sid)


def active_connection_count() -> int:
    with _connections_lock:
        return len(_connections)


def connection_observations() -> list[tuple[float, Mapping[str, Any] | None]]:
    return [(active_connection_count(), None)]


########################################
# Carrier
########################################


@dataclass(frozen=True)
class EventCarrier:
    """The parts of a browser carrier that passed validation."""

    operation_id: str | None = None
    parent: otel_context.Context | None = None


def valid_operation_id(value: Any, sid: str | None = None) -> str | None:
    if not isinstance(value, str) or not REQUEST_ID_PATTERN.fullmatch(value):
        return None
    if sid is not None and value == sid:
        return None
    return value


def parse_carrier(data: Any, sid: str | None = None) -> EventCarrier:
    """Validate ``data["carrier"]``; anything invalid is dropped, never raised."""
    if not isinstance(data, Mapping):
        return EventCarrier()
    carrier = data.get(CARRIER_KEY)
    if not isinstance(carrier, Mapping):
        return EventCarrier()

    operation_id = valid_operation_id(carrier.get(CARRIER_OPERATION_ID), sid)

    parent = None
    traceparent = carrier.get(CARRIER_TRACEPARENT)
    if isinstance(traceparent, str) and len(traceparent) <= _MAX_TRACEPARENT_LENGTH:
        extracted = _propagator.extract(
            {CARRIER_TRACEPARENT: traceparent}, context=otel_context.Context()
        )
        if trace.get_current_span(extracted).get_span_context().is_valid:
            parent = extracted

    return EventCarrier(operation_id=operation_id, parent=parent)


########################################
# Per-event context
########################################


@dataclass
class SocketEvent:
    """Context of one handled event, available to the handler."""

    name: str
    sid: str
    operation_id: str
    user_id: str | None
    span: trace.Span = field(default=trace.INVALID_SPAN)
    outcome: str = OUTCOME_SUCCESS

    def set_outcome(self, outcome: str) -> None:
        if outcome in OUTCOMES:
            self.outcome = outcome

    def reply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``payload`` with the operation ID, so the browser can match replies."""
        return {**payload, CARRIER_OPERATION_ID: self.operation_id}


current_socket_event_var: ContextVar[SocketEvent | None] = ContextVar(
    "current_socket_event", default=None
)


def current_socket_event() -> SocketEvent | None:
    return current_socket_event_var.get()


def _record_event(name: str, outcome: str) -> None:
    metrics = get_metrics_service()
    if metrics is not None and metrics.has_instrument(SOCKETIO_EVENTS):
        metrics.record(SOCKETIO_EVENTS, 1, {"event": name, "outcome": outcome})


@contextmanager
def socket_event_context(
    name: str, sid: str, data: Any = None, *, span: bool = False
) -> Iterator[SocketEvent]:
    carrier = parse_carrier(data, sid)
    event = SocketEvent(
        name=name,
        sid=sid,
        operation_id=carrier.operation_id or uuid.uuid4().hex,
        user_id=connection_user_id(sid),
    )
    event_token = current_socket_event_var.set(event)
    # Start from an empty OTel context: only the validated carrier may parent
    # the span, never whatever the receiving task happened to carry.
    otel_token = otel_context.attach(carrier.parent or otel_context.Context())
    try:
        with log_context(
            request_id=event.operation_id,
            operation_id=event.operation_id,
            user_id=event.user_id,
        ):
            if span:
                with _tracer().start_as_current_span(
                    SPAN_NAME,
                    kind=SpanKind.SERVER,
                    record_exception=False,
                    set_status_on_exception=False,
                ) as event.span:
                    event.span.set_attribute("canchat.socketio.event", name)
                    event.span.set_attribute(REQUEST_ID_ATTRIBUTE, event.operation_id)
                    try:
                        yield event
                    except BaseException as e:
                        _mark(event, e)
                        raise
                    finally:
                        event.span.set_attribute("canchat.outcome", event.outcome)
                        if event.outcome == OUTCOME_ERROR:
                            event.span.set_status(Status(StatusCode.ERROR))
            else:
                try:
                    yield event
                except BaseException as e:
                    _mark(event, e)
                    raise
    finally:
        otel_context.detach(otel_token)
        current_socket_event_var.reset(event_token)
        _record_event(name, event.outcome)


def _mark(event: SocketEvent, error: BaseException) -> None:
    if isinstance(error, asyncio.CancelledError):
        event.outcome = OUTCOME_CANCELLED
        return
    event.outcome = OUTCOME_ERROR
    if event.span.is_recording():
        event.span.set_attribute("error.type", type(error).__name__)


Handler = Callable[..., Awaitable[Any]]
# The first argument of these is the WSGI environ or a disconnect reason.
_NON_PAYLOAD_EVENTS = frozenset({"connect", "disconnect"})


def observe_event(name: str, *, span: bool = False) -> Callable[[Handler], Handler]:
    """Run a ``(sid, data?, ...)`` handler inside ``socket_event_context``.

    ``name`` is the registered event name, so the ``event`` label is bounded
    by the set of handlers. ``span=True`` is for business events only; chatty
    events (``usage``, typing) get context and counters but no span.
    """

    def decorator(handler: Handler) -> Handler:
        signature = inspect.signature(handler)

        @functools.wraps(handler)
        async def wrapper(sid, *args, **kwargs):
            # python-socketio retries ``connect``/``disconnect`` with another
            # arity on ``TypeError``; a mismatch must not count as an event.
            signature.bind(sid, *args, **kwargs)
            data = args[0] if args and name not in _NON_PAYLOAD_EVENTS else None
            with socket_event_context(name, sid, data, span=span):
                return await handler(sid, *args, **kwargs)

        return wrapper

    return decorator
