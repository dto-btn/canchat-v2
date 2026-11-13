"""RAG instrumentation: embeddings, Qdrant operations and retrieval (OBS-28).

``qdrant-client`` has no OpenTelemetry instrumentor, so each logical vector
operation gets a manual ``rag.vector.<operation>`` span. Its HTTP calls
(``httpx``) and the Redis collection lock nest under it, so the span is
``INTERNAL`` and its duration includes lock waits and helper calls such as
``collection_exists``.

* ``rag.embedding``: one call of the configured embedding function. Remote
  providers (Ollama/OpenAI) appear as child ``requests`` HTTP spans.
* ``rag.vector.<operation>``: search, query, get, upsert, delete and
  delete_collection on Qdrant.
* ``rag.retrieve``: one ``query_collection`` (vector) or
  ``query_collection_with_hybrid_search`` (hybrid) call, the parent of the
  embedding and vector spans for a chat retrieval.

Spans carry a bounded collection type, counts and outcomes only: never
collection names (they can be user supplied), query text, document text,
vectors, filters or URLs. Instrumentation failures are logged
and swallowed so they never break retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from open_webui.constants import VECTOR_COLLECTION_PREFIXES
from open_webui.metrics.service import get_metrics_service
from open_webui.observability.chat import (
    OUTCOME_CANCELLED,
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    mark_span_error,
)

log = logging.getLogger(__name__)

TRACER_NAME = "open_webui.rag"

RAG_RETRIEVAL_DURATION = "canchat.rag.retrieval.duration"
RAG_EMBEDDING_DURATION = "canchat.rag.embedding.duration"
RAG_VECTOR_DURATION = "canchat.rag.vector.duration"
RAG_RESULTS = "canchat.rag.results"

VECTOR_DB_SYSTEM = "qdrant"
# ``provider`` label for the in-process SentenceTransformers model (engine "").
LOCAL_EMBEDDING_PROVIDER = "sentence_transformers"
# ``error.type`` for a failed remote embedding: the name of the
# ``retrieval.utils.EmbeddingFailed`` exception, also used if an embedding
# function returns None instead of raising.
EMBEDDING_FAILED = "EmbeddingFailed"

SEARCH_TYPE_VECTOR = "vector"
SEARCH_TYPE_HYBRID = "hybrid"

# ``error.type`` on ``rag.retrieve`` when a collection query failed but the
# retrieval fell back to the remaining results instead of raising.
VECTOR_SEARCH_FAILED = "VectorSearchFailed"

COLLECTION_TYPE_FILE = "file"
COLLECTION_TYPE_WEB_SEARCH = "web-search"
COLLECTION_TYPE_KNOWLEDGE = "knowledge"
COLLECTION_TYPE_OTHER = "other"

# Indirection so tests can drive a deterministic clock.
_monotonic = time.monotonic


def _tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


class RagOperation:
    """Handle for the body of a ``rag_operation`` block."""

    def __init__(self, span: trace.Span):
        self.span = span
        self.result_count: int | None = None
        self.error_type: str | None = None

    def set_result_count(self, count: int | None) -> None:
        self.result_count = count

    def fail(self, error_type: str) -> None:
        """Mark a failure that did not raise (e.g. a provider returned None)."""
        self.error_type = error_type


def _start_span(name: str, attributes: Mapping[str, Any]) -> trace.Span:
    try:
        return _tracer().start_span(
            name,
            kind=SpanKind.INTERNAL,
            attributes={k: v for k, v in attributes.items() if v is not None},
            record_exception=False,
            set_status_on_exception=False,
        )
    except Exception as e:
        log.warning("Failed to start %s span: %s", name, type(e).__name__)
        return trace.INVALID_SPAN


def _record(name: str, value: float, labels: Mapping[str, Any]) -> None:
    metrics = get_metrics_service()
    if metrics is None:
        return
    try:
        metrics.record(name, value, {k: v for k, v in labels.items() if v is not None})
    except Exception as e:
        # Strict mode raises on label misuse: that is a test failure, not a
        # retrieval failure, so only log here.
        log.warning("Failed to record %s: %s", name, type(e).__name__)


@contextmanager
def rag_operation(
    span_name: str,
    *,
    duration_metric: str,
    labels: Mapping[str, Any],
    attributes: Mapping[str, Any] | None = None,
    results_operation: str | None = None,
) -> Iterator[RagOperation]:
    """Span plus duration (and optional result-count) metrics for one operation.

    ``labels`` go on the metrics and must be on the metrics allow-list;
    ``outcome`` and ``error.type`` are added here. ``results_operation`` is the
    ``operation`` label of ``canchat.rag.results`` when a count is recorded.
    """
    started = _monotonic()
    span = _start_span(span_name, attributes or {})
    op = RagOperation(span)
    outcome = OUTCOME_SUCCESS
    try:
        with trace.use_span(
            span,
            end_on_exit=False,
            record_exception=False,
            set_status_on_exception=False,
        ):
            yield op
        if op.error_type is not None:
            outcome = OUTCOME_ERROR
            span.set_attribute("error.type", op.error_type)
            span.set_status(trace.StatusCode.ERROR)
    except asyncio.CancelledError:
        outcome = OUTCOME_CANCELLED
        raise
    except BaseException as e:
        outcome = OUTCOME_ERROR
        op.error_type = type(e).__qualname__
        mark_span_error(span, e)
        raise
    finally:
        try:
            duration = max(0.0, _monotonic() - started)
            span.set_attribute("canchat.outcome", outcome)
            if op.result_count is not None:
                span.set_attribute("canchat.rag.result_count", op.result_count)
            span.end()
            metric_labels = {**labels, "outcome": outcome, "error.type": op.error_type}
            _record(duration_metric, duration, metric_labels)
            if results_operation is not None and op.result_count is not None:
                _record(RAG_RESULTS, op.result_count, {"operation": results_operation})
        except Exception as e:
            log.warning("Failed to finish %s: %s", span_name, type(e).__name__)


def count_ids(result: Any) -> int:
    """Number of records in a ``SearchResult``/``GetResult``.

    Counts every ID whether ``ids`` is nested per query (``[[a, b]]``) or per
    record (``[[a], [b]]``).
    """
    ids = getattr(result, "ids", None)
    if not ids:
        return 0
    return sum(len(i) if isinstance(i, (list, tuple)) else 1 for i in ids)


def count_documents(result: Any) -> int:
    """Number of documents in a merged retrieval dict (``documents[0]``)."""
    try:
        documents = result.get("documents") or []
        return len(documents[0]) if documents else 0
    except Exception:
        return 0


def collection_type(name: str) -> str:
    """Bounded type of a collection; the name itself is never exported."""
    if name.startswith(VECTOR_COLLECTION_PREFIXES.FILE.value):
        return COLLECTION_TYPE_FILE
    if name.startswith(VECTOR_COLLECTION_PREFIXES.WEB_SEARCH.value):
        return COLLECTION_TYPE_WEB_SEARCH
    if name.startswith(VECTOR_COLLECTION_PREFIXES.KNOWLEDGE_BASE.value):
        return COLLECTION_TYPE_KNOWLEDGE
    try:
        # Knowledge bases use their UUID ``id`` as the collection name.
        uuid.UUID(name)
        return COLLECTION_TYPE_KNOWLEDGE
    except ValueError:
        return COLLECTION_TYPE_OTHER


def _collection_name(args: tuple, kwargs: Mapping[str, Any]) -> Any:
    if "collection_name" in kwargs:
        return kwargs["collection_name"]
    return args[0] if args else None


def count_arg(
    name: str, position: int = 1
) -> Callable[[tuple, Mapping[str, Any]], int | None]:
    """``count_items`` callback: length of argument ``name`` (``None`` if absent).

    ``position`` is its index in the caller's positional arguments, after
    ``self`` (``collection_name`` is 0).
    """

    def _count(args: tuple, kwargs: Mapping[str, Any]) -> int | None:
        if name in kwargs:
            value = kwargs[name]
        else:
            value = args[position] if len(args) > position else None
        return len(value) if value is not None else None

    return _count


def vector_operation(
    operation: str,
    *,
    count_results: Callable[[Any], int] | None = None,
    count_items: Callable[[tuple, Mapping[str, Any]], int | None] | None = None,
):
    """Decorate an async Qdrant method with a ``rag.vector.<operation>`` span.

    Apply it outside ``with_qdrant_client`` so the wrapper sees the caller's
    arguments (``collection_name`` first). ``count_results`` maps the return
    value to a result count; ``count_items`` maps the call arguments to the
    number of points written or deleted.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            attributes: dict[str, Any] = {
                "canchat.operation": f"rag.vector.{operation}",
                "db.system.name": VECTOR_DB_SYSTEM,
                "db.operation.name": operation,
            }
            try:
                collection = _collection_name(args, kwargs)
                if isinstance(collection, str):
                    attributes["canchat.rag.collection.type"] = collection_type(
                        collection
                    )
                if count_items is not None:
                    attributes["canchat.rag.item_count"] = count_items(args, kwargs)
                limit = kwargs.get("limit")
                if isinstance(limit, int) and not isinstance(limit, bool):
                    attributes["canchat.rag.limit"] = limit
            except Exception as e:
                log.debug(
                    "Failed to read rag.vector.%s attributes: %s",
                    operation,
                    type(e).__name__,
                )
            with rag_operation(
                f"rag.vector.{operation}",
                duration_metric=RAG_VECTOR_DURATION,
                labels={"operation": operation, "provider": VECTOR_DB_SYSTEM},
                attributes=attributes,
                results_operation=operation if count_results else None,
            ) as op:
                result = await func(self, *args, **kwargs)
                if count_results is not None:
                    try:
                        op.set_result_count(count_results(result))
                    except Exception as e:
                        log.debug(
                            "Failed to count rag.vector.%s results: %s",
                            operation,
                            type(e).__name__,
                        )
                return result

        return wrapper

    return decorator


def _input_count(text: Any) -> int:
    return len(text) if isinstance(text, (list, tuple)) else 1


def instrument_embedding_function(
    func: Callable[..., Any], *, provider: str, model: str | None
) -> Callable[..., Any]:
    """Wrap an embedding function (``query, user=None``) with ``rag.embedding``.

    The function is synchronous and runs wherever the caller runs it: callers
    on the event loop should use ``asyncio.to_thread``, which copies the
    context so the span keeps its parent.
    """
    model_label = model or None

    @wraps(func)
    def embed(query, user=None):
        with rag_operation(
            "rag.embedding",
            duration_metric=RAG_EMBEDDING_DURATION,
            labels={"provider": provider, "model": model_label},
            attributes={
                "canchat.operation": "rag.embedding",
                "canchat.rag.embedding.provider": provider,
                "canchat.rag.embedding.model": model_label,
                "canchat.rag.input_count": _input_count(query),
            },
        ) as op:
            result = func(query, user=user)
            if result is None or (
                isinstance(result, list) and any(r is None for r in result)
            ):
                op.fail(EMBEDDING_FAILED)
            return result

    return embed


@contextmanager
def retrieval_operation(
    search_type: str,
    *,
    collections: int,
    queries: int,
    top_k: int | None,
) -> Iterator[RagOperation]:
    """``rag.retrieve`` span and ``canchat.rag.retrieval.duration``."""
    with rag_operation(
        "rag.retrieve",
        duration_metric=RAG_RETRIEVAL_DURATION,
        labels={"operation": search_type},
        attributes={
            "canchat.operation": "rag.retrieve",
            "canchat.rag.search_type": search_type,
            "canchat.rag.collections.count": collections,
            "canchat.rag.queries.count": queries,
            "canchat.rag.top_k": top_k,
        },
        results_operation="retrieve",
    ) as op:
        yield op
