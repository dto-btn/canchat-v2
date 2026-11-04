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
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span

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
    ):
        self.context = context
        self._metrics = metrics_service
        self.model = model or UNKNOWN
        self.provider = provider
        self.stream = stream
        self.attempts = 0
        self.first_token_at: float | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
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
    ) -> "LLMOperation":
        """Start the operation and its timer; call right before the provider call.

        ``model`` must already be a configured model ID (a bounded label).
        The span is a child of the current span (normally ``chat.operation``)
        but is not made current, so it can outlive the HTTP handler.
        """
        started = _monotonic()
        metadata = metadata if isinstance(metadata, Mapping) else {}
        chat_id = safe_chat_id(metadata.get("chat_id"))
        span = _tracer().start_span("llm.chat")
        for key, value in {
            "canchat.operation": "llm.chat",
            "canchat.llm.provider": provider,
            "canchat.model": model,
            "canchat.stream": bool(stream),
            "canchat.chat.id": chat_id,
        }.items():
            if value is not None:
                span.set_attribute(key, value)
        context = OperationContext(
            span=span,
            started=started,
            chat_id=chat_id,
            message_id=safe_chat_id(metadata.get("message_id")),
        )
        state = getattr(getattr(request, "app", None), "state", None)
        operation = cls(
            context,
            getattr(state, "metrics_service", None),
            model=model,
            provider=provider,
            stream=bool(stream),
        )
        owner = current_chat_operation_var.get()
        if owner is not None and owner.attach(operation):
            operation._owner = owner
        return operation

    @contextmanager
    def attempt(self) -> Iterator[Span]:
        """Span for one network attempt, current so client spans nest under it.

        A failed attempt does not finish the operation: the caller retries or
        finishes it.
        """
        self.attempts += 1
        parent = trace.set_span_in_context(self.span)
        span = _tracer().start_span("llm.attempt", context=parent)
        span.set_attribute("canchat.llm.attempt", self.attempts)
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

    def record_usage(self, input_tokens: Any, output_tokens: Any) -> None:
        input_tokens = _non_negative_int(input_tokens)
        output_tokens = _non_negative_int(output_tokens)
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens

    def observe_response(self, body: Any) -> None:
        """Read usage from a complete (non-streaming) provider response."""
        if isinstance(body, Mapping):
            self._observe_usage(body)

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
                    self._observe_chunk(chunk, fmt)
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
                if self.first_token_at is not None and '"usage"' not in line:
                    continue
            elif self.first_token_at is not None and '"done"' not in line:
                continue

            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, Mapping):
                continue

            if self.first_token_at is None and self._has_content(data, fmt):
                self.first_token_at = _monotonic()
            self._observe_usage(data)
            if fmt == FORMAT_NDJSON and data.get("done") is True:
                self.finish()
                return

    @staticmethod
    def _has_content(data: Mapping[str, Any], fmt: str) -> bool:
        if fmt == FORMAT_NDJSON:
            message = data.get("message")
            return isinstance(message, Mapping) and bool(message.get("content"))
        choices = data.get("choices")
        if not isinstance(choices, list):
            return False
        for choice in choices:
            delta = choice.get("delta") if isinstance(choice, Mapping) else None
            if isinstance(delta, Mapping) and delta.get("content"):
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
            if error is not None:
                mark_span_error(span, error)
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
        values = [
            (LLM_REQUESTS, 1, {**labels, "outcome": outcome}),
            (
                LLM_REQUEST_DURATION,
                duration,
                {**labels, "outcome": outcome, "stream": self.stream},
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
    "LLMOperation",
    "LLM_REQUESTS",
    "LLM_REQUEST_DURATION",
    "LLM_TIME_TO_FIRST_TOKEN",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
]
