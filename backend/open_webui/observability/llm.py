"""LLM provider operation instrumentation (OBS-23).

An ``llm.chat`` operation covers one provider generation: it starts
immediately before the provider HTTP call and ends when the provider response
has been consumed. That end is decided by whoever consumes the response:

* JSON: the provider router, once the body has been read.
* Streams: the instrumented body iterator, read either by the Socket.IO
  background task or by the direct HTTP ``StreamingResponse``. A stream that
  is abandoned is finished with the chat operation that owns it, or as
  ``cancelled`` when its iterator is garbage collected.

Each network attempt has its own ``llm.attempt`` span (the HTTP client span
nests under it); the operation and its metrics are recorded once, however
many attempts it takes. Time to first token (TTFT) is measured from
invocation to the first parsed content token, so response headers, SSE
keepalives, role-only deltas and task start-up are excluded. Reasoning tokens
do not count as content. Non-streaming responses have no TTFT.

Spans never carry prompts, completions or URLs, and no span or INFO log is
emitted per chunk.

OBS-25 adds the approved OpenTelemetry GenAI metadata (``GEN_AI_ATTRIBUTES``)
to the same ``llm.chat`` span. Any other ``gen_ai.*`` key (messages, system
instructions, tool arguments) is never set here and is dropped at export.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import weakref
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from open_webui.constants import TASKS
from open_webui.observability.chat import (
    OUTCOME_CANCELLED,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    mark_span_error,
    safe_chat_id,
)
from open_webui.observability.context import (
    OperationContext,
    current_chat_operation_var,
    log_context,
)

log = logging.getLogger(__name__)

TRACER_NAME = "open_webui.llm"
LLM_REQUESTS = "canchat.llm.requests"
LLM_REQUEST_DURATION = "canchat.llm.request.duration"
LLM_TIME_TO_FIRST_TOKEN = "canchat.llm.time_to_first_token"

PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"

FORMAT_SSE = "sse"
FORMAT_NDJSON = "ndjson"

UNKNOWN = "unknown"

# ``task`` label: user generations vs. task-model calls (title, tags, ...).
TASK_GENERATION = "generation"
TASK_OTHER = "other"
_KNOWN_TASKS = frozenset(task.value for task in TASKS)

# OpenTelemetry GenAI semantic conventions (OBS-25). Only these keys may be
# set; they are also the only ``gen_ai.*`` keys in the export allow-list
# (``tracing.ALLOWED_SPAN_ATTRIBUTES``).
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_ATTRIBUTES = frozenset(
    {
        GEN_AI_OPERATION_NAME,
        GEN_AI_PROVIDER_NAME,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_RESPONSE_FINISH_REASONS,
        GEN_AI_USAGE_INPUT_TOKENS,
        GEN_AI_USAGE_OUTPUT_TOKENS,
    }
)

OPERATION_CHAT = "chat"
OPERATION_TEXT_COMPLETION = "text_completion"
_OPERATION_NAMES = frozenset({OPERATION_CHAT, OPERATION_TEXT_COMPLETION})

# Finish reasons are reported by the provider; anything outside this set is
# recorded as ``other`` so the attribute stays bounded.
FINISH_REASON_OTHER = "other"
_FINISH_REASONS = frozenset(
    {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "function_call",
        "error",
        # Ollama ``done_reason``
        "load",
        "unload",
    }
)
_MAX_FINISH_REASONS = 8
# Model names reported by the provider: an identifier, never free text.
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")

# Indirection so tests can drive a deterministic clock.
_monotonic = time.monotonic


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def _as_text(chunk: Any) -> str:
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk).decode("utf-8", errors="replace")
    return chunk if isinstance(chunk, str) else ""


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _model_name(value: Any) -> str | None:
    if isinstance(value, str) and _MODEL_NAME.fullmatch(value):
        return value
    return None


def _finish_reason(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value if value in _FINISH_REASONS else FINISH_REASON_OTHER


def task_label(task: Any) -> str:
    """Bounded ``task`` label from request metadata."""
    if task is None or task == "":
        return TASK_GENERATION
    task = str(task)
    return task if task in _KNOWN_TASKS else TASK_OTHER


def _has_finish_reason(line: str) -> bool:
    """Cheap pre-check so later stream lines are parsed only for a finish reason."""
    return (
        '"finish_reason"' in line
        and '"finish_reason":null' not in line
        and '"finish_reason": null' not in line
    )


def _set_gen_ai(span: Span, attributes: Mapping[str, Any]) -> None:
    """Set approved ``gen_ai.*`` attributes; unknown keys and ``None`` are dropped."""
    for key, value in attributes.items():
        if value is not None and key in GEN_AI_ATTRIBUTES:
            span.set_attribute(key, value)


class LLMOperation:
    """One provider generation: an ``llm.chat`` span plus metrics, recorded once."""

    def __init__(
        self,
        context: OperationContext,
        metrics_service: Any = None,
        *,
        model: str | None,
        provider: str,
        stream: bool,
        task: str = TASK_GENERATION,
    ):
        self.context = context
        self._metrics = metrics_service
        self.model = model or UNKNOWN
        self.provider = provider
        self.stream = stream
        self.task = task
        self.attempts = 0
        self.first_token_at: float | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.response_model: str | None = None
        self.finish_reasons: list[str] = []
        # error.type of a failure reported in a response that still completed
        # (HTTP error status on a stream, in-band provider error).
        self.failure: str | None = None
        self._owner: Any = None

    @property
    def span(self) -> Span:
        return self.context.span

    @property
    def finished(self) -> bool:
        return self.context.finished

    @classmethod
    def start(
        cls,
        request: Any,
        *,
        provider: str,
        model: str | None,
        stream: bool,
        metadata: Mapping[str, Any] | None = None,
        operation_name: str = OPERATION_CHAT,
        request_model: str | None = None,
        pipeline: bool = False,
        gen_ai_provider: str | None = None,
        metrics_service: Any = None,
    ) -> "LLMOperation":
        """Start the operation and its timer; call right before the provider call.

        ``model`` must already be a configured model ID (a bounded label).
        ``request_model`` is the model name sent to the provider (after base
        model and prefix resolution); it is recorded as ``gen_ai.request.model``
        only when ``model`` is configured. ``pipeline`` marks an OpenAI
        connection that is a Pipelines server. ``gen_ai_provider`` overrides
        ``gen_ai.provider.name`` when the client differs from the ``provider``
        label (CrewAI). Without a ``request``, ``metrics_service`` is used.
        The span is a child of the current span (normally ``chat.operation``)
        but is not made current, so it can outlive the HTTP handler. Never
        raises: if instrumentation fails, a no-op operation is returned so the
        provider call still goes ahead.
        """
        started = _monotonic()
        try:
            metadata = metadata if isinstance(metadata, Mapping) else {}
            task = task_label(metadata.get("task"))
        except Exception:
            metadata, task = {}, TASK_GENERATION
        try:
            chat_id = safe_chat_id(metadata.get("chat_id"))
            span = _tracer().start_span("llm.chat")
            for key, value in {
                "canchat.operation": "llm.chat",
                "canchat.llm.provider": provider,
                "canchat.llm.task": task,
                "canchat.model": model,
                "canchat.stream": bool(stream),
                "canchat.chat.id": chat_id,
                "canchat.llm.pipeline": True if pipeline else None,
            }.items():
                if value is not None:
                    span.set_attribute(key, value)
            _set_gen_ai(
                span,
                {
                    GEN_AI_OPERATION_NAME: (
                        operation_name
                        if operation_name in _OPERATION_NAMES
                        else OPERATION_CHAT
                    ),
                    GEN_AI_PROVIDER_NAME: gen_ai_provider or provider,
                    GEN_AI_REQUEST_MODEL: (
                        _model_name(request_model) if model is not None else None
                    ),
                },
            )
            context = OperationContext(
                span=span,
                started=started,
                chat_id=chat_id,
                message_id=safe_chat_id(metadata.get("message_id")),
            )
            state = getattr(getattr(request, "app", None), "state", None)
            operation = cls(
                context,
                getattr(state, "metrics_service", None) or metrics_service,
                model=model,
                provider=provider,
                stream=bool(stream),
                task=task,
            )
            owner = current_chat_operation_var.get()
            if owner is not None and owner.attach(operation):
                operation._owner = owner
            return operation
        except Exception as e:
            log.warning("Failed to start llm.chat operation: %s", type(e).__name__)
            return cls(
                OperationContext(span=trace.INVALID_SPAN, started=started),
                None,
                model=model,
                provider=provider,
                stream=bool(stream),
                task=task,
            )

    @contextmanager
    def attempt(self) -> Iterator[Span]:
        """Span for one network attempt, current so client spans nest under it.

        A failed attempt does not finish the operation: the caller retries or
        finishes it.
        """
        self.attempts += 1
        try:
            parent = trace.set_span_in_context(self.span)
            span = _tracer().start_span("llm.attempt", context=parent)
            span.set_attribute("canchat.llm.attempt", self.attempts)
        except Exception as e:
            log.warning("Failed to start llm.attempt span: %s", type(e).__name__)
            span = trace.INVALID_SPAN
        with (
            trace.use_span(
                span,
                end_on_exit=True,
                record_exception=False,
                set_status_on_exception=False,
            ),
            log_context(
                operation_id=self.context.operation_id,
                model=self.model,
                provider=self.provider,
            ),
        ):
            try:
                yield span
            except asyncio.CancelledError:
                span.set_attribute("canchat.outcome", OUTCOME_CANCELLED)
                raise
            except BaseException as e:
                mark_span_error(span, e)
                raise

    def mark_failed(self, error_type: str) -> None:
        """Report a failure carried by a response that otherwise completes."""
        if self.failure is None:
            self.failure = error_type

    def record_http_status(self, status: Any) -> None:
        """An error status on a response that is still streamed to the client."""
        if isinstance(status, int) and status >= 400:
            self.mark_failed(str(status))

    def record_usage(self, input_tokens: Any, output_tokens: Any) -> None:
        input_tokens = _non_negative_int(input_tokens)
        output_tokens = _non_negative_int(output_tokens)
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens

    def observe_response(self, body: Any) -> None:
        """Read usage and response metadata from a complete (non-streaming) response."""
        if not isinstance(body, Mapping):
            return
        try:
            self._observe_response_metadata(body)
            self._observe_usage(body)
        except Exception as e:
            # Telemetry must never fail a response the provider completed.
            log.debug("Failed to observe llm response: %s", type(e).__name__)

    def _observe_response_metadata(self, data: Mapping[str, Any]) -> None:
        if self.response_model is None:
            self.response_model = _model_name(data.get("model"))
        reasons = [data.get("done_reason")]
        choices = data.get("choices")
        if isinstance(choices, list):
            reasons.extend(
                choice.get("finish_reason")
                for choice in choices
                if isinstance(choice, Mapping)
            )
        for reason in reasons:
            reason = _finish_reason(reason)
            if (
                reason is not None
                and reason not in self.finish_reasons
                and len(self.finish_reasons) < _MAX_FINISH_REASONS
            ):
                self.finish_reasons.append(reason)

    def _observe_usage(self, data: Mapping[str, Any]) -> None:
        usage = data.get("usage")
        if isinstance(usage, Mapping):
            self.record_usage(
                usage.get("prompt_tokens"), usage.get("completion_tokens")
            )
        elif self.provider == PROVIDER_OLLAMA:
            self.record_usage(data.get("prompt_eval_count"), data.get("eval_count"))

    def wrap_stream(
        self, iterator: AsyncIterator[Any], fmt: str = FORMAT_SSE
    ) -> AsyncIterator[Any]:
        """Hand finalization to whoever reads the provider stream."""
        body = self._instrumented(iterator, fmt)
        finalizer = weakref.finalize(body, self.finish, OUTCOME_CANCELLED)
        finalizer.atexit = False
        return body

    async def _instrumented(self, iterator: AsyncIterator[Any], fmt: str):
        try:
            async for chunk in iterator:
                if not self.finished:
                    try:
                        self._observe_chunk(chunk, fmt)
                    except Exception as e:
                        # Telemetry must never break the response.
                        log.debug("Failed to observe llm chunk: %s", type(e).__name__)
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            self.finish(OUTCOME_CANCELLED)
            raise
        except BaseException as e:
            self.finish(OUTCOME_ERROR, e)
            raise
        else:
            self.finish()

    def _observe_chunk(self, chunk: Any, fmt: str) -> None:
        text = _as_text(chunk)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if fmt == FORMAT_SSE:
                if not line.startswith("data:"):
                    # SSE comments (keepalives), event names and ids
                    continue
                line = line[len("data:") :].strip()
                if line == "[DONE]":
                    self.finish()
                    return
                if (
                    self.first_token_at is not None
                    and '"usage"' not in line
                    and '"error"' not in line
                    and not _has_finish_reason(line)
                ):
                    continue
            elif (
                self.first_token_at is not None
                and '"done"' not in line
                and '"error"' not in line
            ):
                continue

            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, Mapping):
                continue

            if data.get("error"):
                self.mark_failed("provider_error")
            if self.first_token_at is None and self._has_content(data, fmt):
                self.first_token_at = _monotonic()
            self._observe_response_metadata(data)
            self._observe_usage(data)
            if fmt == FORMAT_NDJSON and data.get("done") is True:
                self.finish()
                return

    @staticmethod
    def _has_content(data: Mapping[str, Any], fmt: str) -> bool:
        if fmt == FORMAT_NDJSON:
            # /api/chat sends ``message.content``; /api/generate sends ``response``.
            message = data.get("message")
            if isinstance(message, Mapping) and message.get("content"):
                return True
            return isinstance(data.get("response"), str) and bool(data["response"])
        choices = data.get("choices")
        if not isinstance(choices, list):
            return False
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if isinstance(delta, Mapping) and delta.get("content"):
                return True
            # Legacy ``/v1/completions`` streams carry ``choices[].text``.
            if isinstance(choice.get("text"), str) and choice["text"]:
                return True
        return False

    def finish(
        self, outcome: str = OUTCOME_SUCCESS, error: BaseException | None = None
    ) -> None:
        if not self.context.claim_finish():
            return
        duration = _monotonic() - self.context.started
        if self._owner is not None:
            self._owner.detach(self)
            self._owner = None

        ttft = None
        if self.stream and self.first_token_at is not None:
            ttft = self.first_token_at - self.context.started

        failure = None
        if outcome == OUTCOME_SUCCESS and self.failure is not None:
            outcome, failure = OUTCOME_ERROR, self.failure

        try:
            span = self.span
            span.set_attribute("canchat.outcome", outcome)
            span.set_attribute("canchat.llm.attempts", self.attempts)
            usage_known = (
                self.input_tokens is not None or self.output_tokens is not None
            )
            span.set_attribute("canchat.llm.usage.known", usage_known)
            if self.input_tokens is not None:
                span.set_attribute("canchat.llm.usage.input_tokens", self.input_tokens)
            if self.output_tokens is not None:
                span.set_attribute(
                    "canchat.llm.usage.output_tokens", self.output_tokens
                )
            if ttft is not None:
                span.set_attribute("canchat.llm.time_to_first_token", ttft)
            _set_gen_ai(
                span,
                {
                    GEN_AI_RESPONSE_MODEL: self.response_model,
                    GEN_AI_RESPONSE_FINISH_REASONS: (
                        tuple(self.finish_reasons) if self.finish_reasons else None
                    ),
                    GEN_AI_USAGE_INPUT_TOKENS: self.input_tokens,
                    GEN_AI_USAGE_OUTPUT_TOKENS: self.output_tokens,
                },
            )
            if error is not None:
                mark_span_error(span, error)
            elif failure is not None:
                span.set_attribute("error.type", failure)
                span.set_status(StatusCode.ERROR)
            span.end()
        except Exception as e:
            log.warning("Failed to end llm.chat span: %s", type(e).__name__)

        self._record_metrics(outcome, duration, ttft)

    def _record_metrics(
        self, outcome: str, duration: float, ttft: float | None
    ) -> None:
        if self._metrics is None:
            return
        labels = {"model": self.model, "provider": self.provider}
        outcome_labels = {**labels, "task": self.task, "outcome": outcome}
        values = [
            (LLM_REQUESTS, 1, outcome_labels),
            (
                LLM_REQUEST_DURATION,
                duration,
                {**outcome_labels, "stream": self.stream},
            ),
        ]
        if ttft is not None:
            values.append((LLM_TIME_TO_FIRST_TOKEN, ttft, labels))
        for name, value, attrs in values:
            try:
                if self._metrics.has_instrument(name):
                    self._metrics.record(name, value, attrs)
            except Exception as e:
                # MetricsService raises only in strict (test) mode.
                if getattr(self._metrics, "strict", False):
                    raise
                log.warning("Failed to record %s: %s", name, type(e).__name__)


__all__ = [
    "FORMAT_NDJSON",
    "FORMAT_SSE",
    "GEN_AI_ATTRIBUTES",
    "LLMOperation",
    "LLM_REQUESTS",
    "LLM_REQUEST_DURATION",
    "LLM_TIME_TO_FIRST_TOKEN",
    "OPERATION_CHAT",
    "OPERATION_TEXT_COMPLETION",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "task_label",
]
