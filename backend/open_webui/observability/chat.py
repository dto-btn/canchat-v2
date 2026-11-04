"""Chat request lifecycle instrumentation (OBS-22).

A chat operation covers one ``POST /api/chat/completions``, from payload
processing until the response has been delivered: the last chunk of a direct
HTTP stream, the end of the Socket.IO background task, or the JSON response.
Provider latency is measured separately (``llm.chat``, OBS-23). Title and tag
generation run after the operation has finished, as root spans linked to it,
so they are never counted as extra chat operations.

Span attributes are allow-listed here, before they reach the SDK, because
in-process span processors see raw spans: model ID, chat ID, feature flags,
counts and the outcome. Prompts, queries, file names, URLs and tool names are
never recorded. Exceptions are recorded by type only.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
import weakref
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Link, Span, StatusCode

from open_webui.observability.context import current_chat_operation_var

log = logging.getLogger(__name__)

TRACER_NAME = "open_webui.chat"
CHAT_OPERATION_DURATION = "canchat.chat.operation.duration"

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_CANCELLED = "cancelled"

# Feature flags from the chat form that are recorded as booleans.
CHAT_FEATURES = ("web_search", "wiki_grounding", "image_generation")

_SCALAR_TYPES = (str, bool, int, float)

# Chat ID the frontend uses for temporary (unsaved) chats.
TEMPORARY_CHAT_ID = "local"


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def _set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        if value is not None and isinstance(value, _SCALAR_TYPES):
            span.set_attribute(key, value)


def mark_span_error(span: Span, error: BaseException) -> None:
    """Record a failure by exception type only: messages can carry content."""
    span.set_attribute("error.type", type(error).__qualname__)
    span.set_status(StatusCode.ERROR)


def feature_attributes(features: Mapping[str, Any] | None) -> dict[str, bool]:
    features = features if isinstance(features, Mapping) else {}
    return {
        f"canchat.feature.{name}": bool(features.get(name)) for name in CHAT_FEATURES
    }


def count_items(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def safe_chat_id(value: Any) -> str | None:
    """Return ``value`` only if it has the shape of a chat ID.

    The chat ID comes from the request body, so free text (possibly prompt
    content) must not reach span attributes. Saved chats have UUID IDs and
    temporary chats use ``local``.
    """
    if not isinstance(value, str):
        return None
    if value == TEMPORARY_CHAT_ID:
        return value
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


@contextmanager
def chat_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    follows: Span | None = None,
) -> Iterator[Span]:
    """Span for one step of a chat operation.

    With ``follows``, the span starts a new trace linked to that span instead
    of being its child: for follow-up work such as title generation that runs
    after the chat operation has ended. Failures are marked by exception type;
    cancellation is not an error.
    """
    kwargs: dict[str, Any] = {}
    if follows is not None:
        kwargs["context"] = otel_context.Context()
        span_context = follows.get_span_context()
        if span_context.is_valid:
            kwargs["links"] = [Link(span_context)]
    with _tracer().start_as_current_span(
        name, record_exception=False, set_status_on_exception=False, **kwargs
    ) as span:
        _set_attributes(span, attributes or {})
        try:
            yield span
        except asyncio.CancelledError:
            span.set_attribute("canchat.outcome", OUTCOME_CANCELLED)
            raise
        except BaseException as e:
            mark_span_error(span, e)
            raise


def known_model_id(request: Any, model_id: Any) -> str | None:
    """Return ``model_id`` only if it is a configured model.

    The model field is user input; an unknown value could be free text and
    would also be an unbounded metric label.
    """
    models = getattr(
        getattr(getattr(request, "app", None), "state", None), "MODELS", None
    )
    if isinstance(model_id, str) and isinstance(models, Mapping) and model_id in models:
        return model_id
    return None


class ChatOperation:
    """One end-to-end chat operation: a span plus a duration recorded once.

    The span outlives the HTTP handler when the response is delivered by a
    background task or a streaming body, so it is started detached and made
    current with :meth:`activate`. Whichever path delivers the response calls
    :meth:`finish`; later calls are ignored.
    """

    def __init__(
        self,
        span: Span,
        metrics_service: Any = None,
        *,
        stream: bool = False,
        started: float | None = None,
    ):
        self.span = span
        self._metrics = metrics_service
        self._stream = stream
        self._model: str | None = None
        self._started = time.monotonic() if started is None else started
        self._finished = False
        # Provider operations still consuming a response for this chat.
        self._children: list[Any] = []

    @classmethod
    def start(cls, request: Any, form_data: Mapping[str, Any]) -> "ChatOperation":
        started = time.monotonic()
        stream = bool(form_data.get("stream", False))
        span = _tracer().start_span("chat.operation")
        _set_attributes(
            span,
            {
                "canchat.operation": "chat",
                "canchat.stream": stream,
                "canchat.chat.id": safe_chat_id(form_data.get("chat_id")),
                **feature_attributes(form_data.get("features")),
            },
        )
        state = getattr(getattr(request, "app", None), "state", None)
        return cls(
            span,
            getattr(state, "metrics_service", None),
            stream=stream,
            started=started,
        )

    @property
    def finished(self) -> bool:
        return self._finished

    def set_model(self, model_id: str | None) -> None:
        if model_id:
            self._model = model_id
            self.span.set_attribute("canchat.model", model_id)

    def set_response_mode(self, mode: str) -> None:
        self.span.set_attribute("canchat.response.mode", mode)

    def attach(self, child: Any) -> bool:
        """Finish ``child`` with this operation if it is still open by then.

        Covers a provider stream that is never read to the end, e.g. a body
        the client abandons. Returns ``False`` once this operation has ended.
        """
        if self._finished:
            return False
        self._children.append(child)
        return True

    def detach(self, child: Any) -> None:
        try:
            self._children.remove(child)
        except ValueError:
            pass

    def finish_when_collected(self, obj: Any) -> None:
        """Finish as ``cancelled`` when ``obj`` is garbage collected.

        Fallback for a streaming body the server never starts reading (client
        gone before the first chunk): a generator that never started runs no
        ``except``/``else`` block when closed. No-op if already finished.
        """
        finalizer = weakref.finalize(obj, self.finish, OUTCOME_CANCELLED)
        finalizer.atexit = False

    @contextmanager
    def activate(self) -> Iterator["ChatOperation"]:
        """Make the operation span current without ending it on exit.

        Anything that escapes the block finishes the operation; a normal exit
        leaves it open for the delivery path to finish.
        """
        token = current_chat_operation_var.set(self)
        try:
            with trace.use_span(
                self.span,
                end_on_exit=False,
                record_exception=False,
                set_status_on_exception=False,
            ):
                try:
                    yield self
                except asyncio.CancelledError:
                    self.finish(OUTCOME_CANCELLED)
                    raise
                except BaseException as e:
                    self.finish(OUTCOME_ERROR, e)
                    raise
        finally:
            current_chat_operation_var.reset(token)

    def finish(
        self, outcome: str = OUTCOME_SUCCESS, error: BaseException | None = None
    ) -> None:
        if self._finished:
            return
        self._finished = True
        duration = time.monotonic() - self._started
        # Children end first so they never end after their parent span.
        for child in list(self._children):
            try:
                child.finish(outcome, error)
            except Exception as e:
                if getattr(self._metrics, "strict", False):
                    raise
                log.warning("Failed to finish child operation: %s", type(e).__name__)
        self._children.clear()
        try:
            self.span.set_attribute("canchat.outcome", outcome)
            if error is not None:
                mark_span_error(self.span, error)
            self.span.end()
        except Exception as e:
            log.warning("Failed to end chat operation span: %s", type(e).__name__)

        if self._metrics is None:
            return
        attrs: dict[str, Any] = {
            "model": self._model or "unknown",
            "stream": self._stream,
            "outcome": outcome,
        }
        if error is not None:
            attrs["error.type"] = type(error).__qualname__
        try:
            if self._metrics.has_instrument(CHAT_OPERATION_DURATION):
                self._metrics.record(CHAT_OPERATION_DURATION, duration, attrs)
        except Exception as e:
            # MetricsService raises only in strict (test) mode.
            if getattr(self._metrics, "strict", False):
                raise
            log.warning("Failed to record chat duration: %s", type(e).__name__)


class _NoopChatOperation(ChatOperation):
    """Stand-in when a caller did not start an operation (e.g. tests, tasks)."""

    def __init__(self):
        super().__init__(trace.INVALID_SPAN)

    def finish(
        self, outcome: str = OUTCOME_SUCCESS, error: BaseException | None = None
    ) -> None:
        self._finished = True


def noop_operation() -> ChatOperation:
    return _NoopChatOperation()


__all__ = [
    "CHAT_OPERATION_DURATION",
    "ChatOperation",
    "OUTCOME_CANCELLED",
    "OUTCOME_ERROR",
    "OUTCOME_SUCCESS",
    "chat_span",
    "count_items",
    "feature_attributes",
    "known_model_id",
    "mark_span_error",
    "noop_operation",
    "safe_chat_id",
]
