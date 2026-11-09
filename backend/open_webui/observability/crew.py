"""CrewAI operation instrumentation (OBS-26).

A crew operation covers one ``crew-mcp-query`` Socket.IO event, from the
authenticated handler to the result or error sent back on the socket. It is
independent of the HTTP chat path: it has its own ``crew.operation`` span,
timer and finalization, recorded exactly once whatever the outcome.

The crew runs in a thread pool. :func:`run_in_executor` copies the current
context explicitly into the worker, so the operation span, the log context
(operation, user and chat IDs) and ``current_crew_operation_var`` cross the
executor boundary; concurrent crews each carry their own copy.

Inside the worker:

* ``crew.run`` and ``crew.kickoff`` (:func:`crew_span`) cover routing and each
  specialist crew;
* model invocations are ``llm.chat`` operations (``observability.llm``) with
  an ``llm.attempt`` child, so the Azure Core ``requests`` client span nests
  under them (the client identified in OBS-03);
* MCP tool calls are ``crew.tool`` spans.

Model and tool spans come from traced subclasses applied to the per-run LLM
and to per-run copies of the MCP tools (:func:`trace_crew_llm`,
:func:`trace_crew_tools`). They keep no per-run state: everything is read from
the current context, so shared MCP adapters are never modified.

Spans carry only bounded identifiers (model, MCP server and tool names, chat
ID, internal user ID), counts, token usage and the outcome. Prompts, tool
arguments and results, crew output and tokens are never recorded; errors are
recorded by exception type only.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode
from pydantic import BaseModel

from open_webui.observability.chat import (
    OUTCOME_CANCELLED,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    mark_span_error,
    safe_chat_id,
)
from open_webui.observability.context import (
    OperationContext,
    current_crew_operation_var,
    log_context,
)
from open_webui.observability.llm import LLMOperation

log = logging.getLogger(__name__)

TRACER_NAME = "open_webui.crew"
CREW_OPERATION_DURATION = "canchat.crew.operation.duration"

# ``gen_ai.provider.name`` for CrewAI providers: the native Azure provider
# uses the Azure AI Inference SDK (OBS-03).
_GEN_AI_PROVIDERS = {"azure": "azure.ai.inference"}

OTHER = "other"
UNKNOWN = "unknown"

# Bounded identifiers (model, server and tool names): no spaces or ``@``, so
# free text and email addresses never reach span attributes or metric labels.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_USER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

# Indirection so tests can drive a deterministic clock.
_monotonic = time.monotonic

# Set while an instrumented LLM call runs, so a provider that calls itself
# again is not counted twice.
_llm_call_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "crew_llm_call_active", default=False
)

# Traced subclasses are cached on their base class (in its own ``__dict__``),
# not in a module-level registry: MCP adapters define a new tool class on each
# connection (every SharePoint run), and the cache must not keep those classes
# and their closed sessions alive.
_TRACED_CACHE = "__canchat_traced_classes__"
_TRACED_MARKER = "__canchat_traced__"
_traced_classes_lock = threading.Lock()


def _cached_traced_class(base: type, key: Any) -> type | None:
    cache = base.__dict__.get(_TRACED_CACHE)
    return cache.get(key) if isinstance(cache, dict) else None


def _cache_traced_class(base: type, key: Any, traced: type) -> None:
    cache = base.__dict__.get(_TRACED_CACHE)
    if not isinstance(cache, dict):
        cache = {}
        type.__setattr__(base, _TRACED_CACHE, cache)
    cache[key] = traced


def _is_traced(cls: type) -> bool:
    return cls.__dict__.get(_TRACED_MARKER) is True


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def bounded_name(value: Any) -> str | None:
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
        return value
    return None


def _safe_user_id(value: Any) -> str | None:
    if isinstance(value, str) and _USER_ID.fullmatch(value):
        return value
    return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


def _default_metrics_service() -> Any:
    from open_webui.metrics.service import get_metrics_service

    return get_metrics_service()


class CrewOperation:
    """One ``crew-mcp-query`` event: a span plus a duration, recorded once."""

    def __init__(
        self,
        context: OperationContext,
        metrics_service: Any = None,
        *,
        user_id: str | None = None,
    ):
        self.context = context
        self._metrics = metrics_service
        self.user_id = user_id
        self.model: str | None = None
        # error.type of a failure the crew reported as a result (for example a
        # SharePoint error turned into an apology message).
        self.failure: str | None = None
        self._result_usage: tuple[int, int] | None = None
        self._llm_input_tokens: int | None = None
        self._llm_output_tokens: int | None = None
        self._lock = threading.Lock()

    @property
    def span(self) -> Span:
        return self.context.span

    @property
    def operation_id(self) -> str:
        return self.context.operation_id

    @property
    def chat_id(self) -> str | None:
        return self.context.chat_id

    @property
    def finished(self) -> bool:
        return self.context.finished

    @classmethod
    def start(
        cls,
        *,
        user_id: Any,
        chat_id: Any = None,
        selected_tools: Any = None,
        metrics_service: Any = None,
    ) -> "CrewOperation":
        """Start the operation and its timer for an authenticated socket event.

        ``user_id`` must come from the server-side socket session, never from
        the event payload. Never raises: if instrumentation fails, an
        operation with a non-recording span is returned.
        """
        started = _monotonic()
        user_id = _safe_user_id(user_id)
        chat_id = safe_chat_id(chat_id)
        if metrics_service is None:
            try:
                metrics_service = _default_metrics_service()
            except Exception:
                metrics_service = None
        try:
            span = _tracer().start_span("crew.operation")
            _set_attributes(
                span,
                {
                    "canchat.operation": "crew",
                    "canchat.user.id": user_id,
                    "canchat.chat.id": chat_id,
                    "canchat.crew.tools.selected": (
                        len(selected_tools)
                        if isinstance(selected_tools, (list, tuple))
                        else 0
                    ),
                },
            )
        except Exception as e:
            log.warning("Failed to start crew operation: %s", type(e).__name__)
            span = trace.INVALID_SPAN
        return cls(
            OperationContext(span=span, started=started, chat_id=chat_id),
            metrics_service,
            user_id=user_id,
        )

    def set_model(self, model: Any) -> None:
        model = bounded_name(model)
        if model:
            self.model = model
            self.span.set_attribute("canchat.model", model)

    def set_server(self, server: Any) -> None:
        server = bounded_name(server)
        if server:
            self.span.set_attribute("canchat.mcp.server", server)

    def fail(self, error_type: str) -> None:
        """Report a failure that still produced a response for the user."""
        if self.failure is None:
            self.failure = bounded_name(error_type) or OTHER

    def record_usage(self, usage: Any) -> None:
        """Token usage reported by the crew result (authoritative when present)."""
        if not isinstance(usage, Mapping):
            return
        prompt = _non_negative_int(usage.get("prompt_tokens"))
        completion = _non_negative_int(usage.get("completion_tokens"))
        total = _non_negative_int(usage.get("total_tokens"))
        # The crew reports zeros when usage is unavailable: that is unknown.
        if prompt is None and completion is None:
            return
        if not total and not prompt and not completion:
            return
        self._result_usage = (prompt or 0, completion or 0)

    def add_llm_usage(self, input_tokens: Any, output_tokens: Any) -> None:
        """Usage of one model invocation, the fallback when the crew fails."""
        input_tokens = _non_negative_int(input_tokens)
        output_tokens = _non_negative_int(output_tokens)
        with self._lock:
            if input_tokens is not None:
                self._llm_input_tokens = (self._llm_input_tokens or 0) + input_tokens
            if output_tokens is not None:
                self._llm_output_tokens = (self._llm_output_tokens or 0) + output_tokens

    def usage(self) -> tuple[int | None, int | None]:
        if self._result_usage is not None:
            return self._result_usage
        with self._lock:
            return self._llm_input_tokens, self._llm_output_tokens

    @contextmanager
    def activate(self) -> Iterator["CrewOperation"]:
        """Make the operation current for the handler and finish it on exit.

        A normal exit finishes it as ``success`` (or ``error`` after
        :meth:`fail`), an exception as ``error`` and cancellation as
        ``cancelled``.
        """
        token = current_crew_operation_var.set(self)
        try:
            with (
                trace.use_span(
                    self.span,
                    end_on_exit=False,
                    record_exception=False,
                    set_status_on_exception=False,
                ),
                log_context(
                    operation_id=self.operation_id,
                    user_id=self.user_id,
                    chat_id=self.chat_id,
                ),
            ):
                try:
                    yield self
                except asyncio.CancelledError:
                    self.finish(OUTCOME_CANCELLED)
                    raise
                except BaseException as e:
                    self.finish(OUTCOME_ERROR, e)
                    raise
                else:
                    self.finish()
        finally:
            current_crew_operation_var.reset(token)

    def finish(
        self, outcome: str = OUTCOME_SUCCESS, error: BaseException | None = None
    ) -> None:
        if not self.context.claim_finish():
            return
        duration = _monotonic() - self.context.started
        failure = None
        if outcome == OUTCOME_SUCCESS and self.failure is not None:
            outcome, failure = OUTCOME_ERROR, self.failure
        error_type = type(error).__qualname__ if error is not None else failure

        try:
            span = self.span
            span.set_attribute("canchat.outcome", outcome)
            input_tokens, output_tokens = self.usage()
            span.set_attribute(
                "canchat.llm.usage.known",
                input_tokens is not None or output_tokens is not None,
            )
            if input_tokens is not None:
                span.set_attribute("canchat.llm.usage.input_tokens", input_tokens)
            if output_tokens is not None:
                span.set_attribute("canchat.llm.usage.output_tokens", output_tokens)
            if error is not None:
                mark_span_error(span, error)
            elif failure is not None:
                span.set_attribute("error.type", failure)
                span.set_status(StatusCode.ERROR)
            span.end()
        except Exception as e:
            log.warning("Failed to end crew operation span: %s", type(e).__name__)

        if self._metrics is None:
            return
        attrs: dict[str, Any] = {"model": self.model or UNKNOWN, "outcome": outcome}
        if error_type is not None:
            attrs["error.type"] = error_type
        try:
            if self._metrics.has_instrument(CREW_OPERATION_DURATION):
                self._metrics.record(CREW_OPERATION_DURATION, duration, attrs)
        except Exception as e:
            # MetricsService raises only in strict (test) mode.
            if getattr(self._metrics, "strict", False):
                raise
            log.warning("Failed to record crew duration: %s", type(e).__name__)


def current_crew_operation() -> CrewOperation | None:
    return current_crew_operation_var.get()


def run_in_executor(
    loop: asyncio.AbstractEventLoop,
    executor: Any,
    func: Callable[..., Any],
    *args: Any,
) -> "asyncio.Future[Any]":
    """``loop.run_in_executor`` with an explicit copy of the current context.

    The worker sees the current span, log context and crew operation as they
    were when the call was made; changes it makes do not leak back.
    """
    context = contextvars.copy_context()
    return loop.run_in_executor(executor, functools.partial(context.run, func, *args))


@contextmanager
def crew_span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """Child span of the current context for one step of a crew run.

    Failures are marked by exception type; cancellation is not an error.
    """
    with _tracer().start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
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


def mark_crew_failed(error: BaseException) -> None:
    """Record a crew failure that was turned into a user-facing message."""
    mark_span_error(trace.get_current_span(), error)
    operation = current_crew_operation()
    if operation is not None:
        operation.fail(type(error).__qualname__)


# --- Model invocations -------------------------------------------------------


def _llm_token_totals(llm: Any) -> tuple[int | None, int | None]:
    """CrewAI's cumulative per-instance usage; diffed around each call."""
    try:
        usage = getattr(llm, "_token_usage", None)
    except Exception:
        return None, None
    if not isinstance(usage, Mapping):
        return None, None
    return (
        _non_negative_int(usage.get("prompt_tokens")),
        _non_negative_int(usage.get("completion_tokens")),
    )


def _begin_llm(llm: Any) -> tuple[LLMOperation, tuple[int | None, int | None]]:
    provider = bounded_name(getattr(llm, "provider", None)) or UNKNOWN
    request_model = bounded_name(getattr(llm, "model", None))
    model = f"{provider}/{request_model}" if request_model else None
    crew = current_crew_operation()
    operation = LLMOperation.start(
        None,
        provider=provider,
        model=bounded_name(model),
        stream=getattr(llm, "stream", False) is True,
        metadata={"chat_id": crew.chat_id if crew is not None else None},
        request_model=request_model,
        gen_ai_provider=_GEN_AI_PROVIDERS.get(provider, provider),
        metrics_service=(
            crew._metrics if crew is not None else _default_metrics_service()
        ),
    )
    return operation, _llm_token_totals(llm)


def _end_llm(
    operation: LLMOperation,
    llm: Any,
    before: tuple[int | None, int | None],
    error: BaseException | None = None,
) -> None:
    try:
        after = _llm_token_totals(llm)
        delta = [
            a - b if a is not None and b is not None and a >= b else None
            for a, b in zip(after, before)
        ]
        operation.record_usage(*delta)
        crew = current_crew_operation()
        if crew is not None:
            crew.add_llm_usage(*delta)
    except Exception as e:
        log.debug("Failed to read crew llm usage: %s", type(e).__name__)
    if error is None:
        operation.finish()
    elif isinstance(error, asyncio.CancelledError):
        operation.finish(OUTCOME_CANCELLED)
    else:
        operation.finish(OUTCOME_ERROR, error)


def _traced_llm_class(base: type) -> type:
    if _is_traced(base):
        return base
    with _traced_classes_lock:
        traced = _cached_traced_class(base, None)
        if traced is not None:
            return traced

        def call(self, *args: Any, **kwargs: Any) -> Any:
            parent = super(traced, self).call
            if _llm_call_active.get():
                return parent(*args, **kwargs)
            operation, before = _begin_llm(self)
            token = _llm_call_active.set(True)
            try:
                with operation.attempt():
                    result = parent(*args, **kwargs)
            except BaseException as e:
                _end_llm(operation, self, before, e)
                raise
            finally:
                _llm_call_active.reset(token)
            _end_llm(operation, self, before)
            return result

        async def acall(self, *args: Any, **kwargs: Any) -> Any:
            parent = super(traced, self).acall
            if _llm_call_active.get():
                return await parent(*args, **kwargs)
            operation, before = _begin_llm(self)
            token = _llm_call_active.set(True)
            try:
                with operation.attempt():
                    result = await parent(*args, **kwargs)
            except BaseException as e:
                _end_llm(operation, self, before, e)
                raise
            finally:
                _llm_call_active.reset(token)
            _end_llm(operation, self, before)
            return result

        namespace: dict[str, Any] = {
            "__module__": __name__,
            _TRACED_MARKER: True,
            "call": call,
        }
        if callable(getattr(base, "acall", None)):
            namespace["acall"] = acall
        traced = type(f"Traced{base.__name__}", (base,), namespace)
        _cache_traced_class(base, None, traced)
        return traced


def trace_crew_llm(llm: Any) -> Any:
    """Trace each model invocation of a per-run CrewAI LLM, in place.

    Anything that is not a CrewAI (pydantic) LLM is returned unchanged.
    """
    try:
        if not isinstance(llm, BaseModel) or not callable(getattr(llm, "call", None)):
            return llm
        llm.__class__ = _traced_llm_class(type(llm))
    except Exception as e:
        log.warning("Failed to instrument crew llm: %s", type(e).__name__)
    return llm


# --- MCP tools ---------------------------------------------------------------


def _traced_tool_class(base: type, server: str | None) -> type:
    with _traced_classes_lock:
        traced = _cached_traced_class(base, server)
        if traced is not None:
            return traced

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            with crew_span(
                "crew.tool",
                {
                    "canchat.operation": "crew.tool",
                    "canchat.mcp.server": server,
                    "canchat.mcp.tool": bounded_name(getattr(self, "name", None))
                    or OTHER,
                },
            ):
                return super(traced, self)._run(*args, **kwargs)

        traced = type(
            f"Traced{base.__name__}",
            (base,),
            {"__module__": __name__, _TRACED_MARKER: True, "_run": _run},
        )
        _cache_traced_class(base, server, traced)
        return traced


def trace_crew_tool(tool: Any, server: str | None = None) -> Any:
    """Per-run copy of a CrewAI tool whose calls are ``crew.tool`` spans.

    The original (possibly shared) tool is not modified. Anything that is not
    a CrewAI (pydantic) tool is returned unchanged.
    """
    try:
        if not isinstance(tool, BaseModel) or not callable(getattr(tool, "_run", None)):
            return tool
        server = bounded_name(server)
        base = type(tool)
        if _is_traced(base):
            return tool
        copy = tool.model_copy()
        copy.__class__ = _traced_tool_class(base, server)
        return copy
    except Exception as e:
        log.warning("Failed to instrument crew tool: %s", type(e).__name__)
        return tool


def trace_crew_tools(tools: Iterable[Any], server: str | None = None) -> list[Any]:
    return [trace_crew_tool(tool, server) for tool in tools]


__all__ = [
    "CREW_OPERATION_DURATION",
    "CrewOperation",
    "bounded_name",
    "crew_span",
    "current_crew_operation",
    "mark_crew_failed",
    "run_in_executor",
    "trace_crew_llm",
    "trace_crew_tool",
    "trace_crew_tools",
]
