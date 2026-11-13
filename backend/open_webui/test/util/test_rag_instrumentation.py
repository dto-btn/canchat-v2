"""OBS-28: RAG embedding, Qdrant and retrieval spans and metrics.

The Qdrant ``AsyncQdrantClient``, the collection lock manager and the
embedding model are stubbed. Spans go through the production
``SanitizingSpanExporter`` into an in-memory exporter; metrics go through
``MetricsService`` (strict) into an in-memory metric reader.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.metrics.service import MetricsService
from open_webui.observability import rag as rag_obs
from open_webui.observability.providers import create_providers
from open_webui.retrieval import utils as retrieval_utils
from open_webui.retrieval.vector.dbs import qdrant as qdrant_module
from open_webui.test.util.test_chat_instrumentation import otel_settings

COLLECTION = "file-0b8a4d6e"
QUERY = "obs28 secret query"
DOC = "obs28 secret document"
VECTOR = [0.125, 0.25]
ALLOWED_RAG_ATTRIBUTES = {
    "canchat.operation",
    "canchat.outcome",
    "canchat.rag.result_count",
    "canchat.rag.item_count",
    "canchat.rag.limit",
    "canchat.rag.input_count",
    "canchat.rag.embedding.provider",
    "canchat.rag.embedding.model",
    "canchat.rag.search_type",
    "canchat.rag.collections.count",
    "canchat.rag.queries.count",
    "canchat.rag.top_k",
    "db.system.name",
    "db.operation.name",
    "canchat.rag.collection.type",
    "error.type",
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Telemetry:
    def __init__(self):
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.bundle = create_providers(
            otel_settings(),
            metric_readers=[self.reader],
            span_exporters=[self.exporter],
        )
        self.metrics = MetricsService(
            self.bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True
        )

    def spans(self, name=None):
        spans = self.exporter.get_finished_spans()
        return [s for s in spans if name is None or s.name == name]

    def points(self, metric_name):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == metric_name:
                        return {
                            tuple(sorted(p.attributes.items())): p
                            for p in metric.data.data_points
                        }
        return {}


@pytest.fixture
def telemetry(monkeypatch):
    t = Telemetry()
    monkeypatch.setattr(
        rag_obs,
        "_tracer",
        lambda: t.bundle.tracer_provider.get_tracer(rag_obs.TRACER_NAME),
    )
    monkeypatch.setattr(rag_obs, "get_metrics_service", lambda: t.metrics)
    yield t
    t.bundle.shutdown()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(rag_obs, "_monotonic", c)
    return c


class FakeQdrant:
    """Stub ``AsyncQdrantClient``; ``delay`` advances the test clock per call."""

    def __init__(self, clock):
        self.clock = clock
        self.delay = 0.0
        self.fail = None
        self.points = [
            SimpleNamespace(id="a", score=0.9, payload={"text": DOC, "metadata": {}}),
            SimpleNamespace(id="b", score=0.8, payload={"text": DOC, "metadata": {}}),
        ]
        self.upserted = []

    async def _call(self):
        self.clock.now += self.delay
        if self.fail:
            raise self.fail

    async def query_points(self, **kwargs):
        await self._call()
        return SimpleNamespace(points=self.points)

    async def scroll(self, **kwargs):
        await self._call()
        return self.points, None

    async def count(self, **kwargs):
        await self._call()
        return SimpleNamespace(count=len(self.points))

    async def collection_exists(self, **kwargs):
        return True

    async def upsert(self, collection_name, points, wait):
        await self._call()
        self.upserted.extend(points)

    async def delete(self, **kwargs):
        await self._call()

    async def delete_collection(self, **kwargs):
        await self._call()
        return True


class FakeLocks:
    @asynccontextmanager
    async def acquire_lock(self, collection_name, timeout_secs=30):
        yield


@pytest.fixture
def qdrant(monkeypatch, clock):
    fake = FakeQdrant(clock)
    client = qdrant_module.QdrantClient()

    async def get_client():
        return fake

    monkeypatch.setattr(client, "_get_client", get_client)
    monkeypatch.setattr(qdrant_module, "_collection_lock_manager", FakeLocks())
    monkeypatch.setattr(retrieval_utils, "VECTOR_DB_CLIENT", client)
    return SimpleNamespace(client=client, fake=fake)


class FakeModel:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def encode(self, query):
        self.calls.append(query)
        self.clock.now += 0.25
        value = [VECTOR for _ in query] if isinstance(query, list) else VECTOR
        return SimpleNamespace(tolist=lambda: value)


def assert_no_content(span):
    assert set(span.attributes) <= ALLOWED_RAG_ATTRIBUTES, span.attributes
    for value in span.attributes.values():
        text = str(value)
        assert QUERY not in text and DOC not in text and "0.125" not in text
    assert not span.events


def labels(**kwargs):
    return tuple(sorted(kwargs.items()))


def test_search_span_and_metrics(telemetry, qdrant, clock):
    qdrant.fake.delay = 0.3

    result = asyncio.run(
        qdrant.client.search(collection_name=COLLECTION, vectors=[VECTOR], limit=5)
    )

    assert rag_obs.count_ids(result) == 2
    (span,) = telemetry.spans("rag.vector.search")
    assert span.status.status_code == StatusCode.UNSET
    assert span.attributes["db.system.name"] == "qdrant"
    assert span.attributes["db.operation.name"] == "search"
    assert span.attributes["canchat.rag.collection.type"] == "file"
    assert span.attributes["canchat.rag.result_count"] == 2
    assert span.attributes["canchat.rag.limit"] == 5
    assert span.attributes["canchat.outcome"] == "success"
    assert_no_content(span)

    durations = telemetry.points(rag_obs.RAG_VECTOR_DURATION)
    point = durations[labels(operation="search", provider="qdrant", outcome="success")]
    assert point.count == 1
    assert point.sum == pytest.approx(0.3)
    results = telemetry.points(rag_obs.RAG_RESULTS)
    assert results[labels(operation="search")].sum == 2


def test_query_and_get_record_result_counts(telemetry, qdrant):
    asyncio.run(qdrant.client.get(collection_name=COLLECTION))
    qdrant.fake.points = []
    asyncio.run(
        qdrant.client.query(collection_name=COLLECTION, filter={"hash": "secret"})
    )

    (get,) = telemetry.spans("rag.vector.get")
    assert get.attributes["canchat.rag.result_count"] == 2
    (query,) = telemetry.spans("rag.vector.query")
    assert query.attributes["canchat.rag.result_count"] == 0
    for span in (get, query):
        assert_no_content(span)
        assert all("secret" not in str(v) for v in span.attributes.values())
    results = telemetry.points(rag_obs.RAG_RESULTS)
    assert results[labels(operation="get")].sum == 2
    assert results[labels(operation="query")].sum == 0


def test_insert_and_delete_spans(telemetry, qdrant):
    items = [
        {"id": str(i), "text": DOC, "vector": [VECTOR], "metadata": {}}
        for i in range(3)
    ]

    asyncio.run(qdrant.client.insert(collection_name=COLLECTION, items=items))
    asyncio.run(qdrant.client.delete(collection_name=COLLECTION, ids=["1", "2"]))
    asyncio.run(qdrant.client.delete_collection(collection_name=COLLECTION))

    assert len(qdrant.fake.upserted) == 3
    (upsert,) = telemetry.spans("rag.vector.upsert")
    assert upsert.attributes["canchat.rag.item_count"] == 3
    (delete,) = telemetry.spans("rag.vector.delete")
    assert delete.attributes["canchat.rag.item_count"] == 2
    (drop,) = telemetry.spans("rag.vector.delete_collection")
    assert drop.attributes["canchat.rag.collection.type"] == "file"
    for span in (upsert, delete, drop):
        assert "canchat.rag.result_count" not in span.attributes
        assert_no_content(span)

    durations = telemetry.points(rag_obs.RAG_VECTOR_DURATION)
    for operation in ("upsert", "delete", "delete_collection"):
        key = labels(operation=operation, provider="qdrant", outcome="success")
        assert durations[key].count == 1
    assert labels(operation="upsert") not in telemetry.points(rag_obs.RAG_RESULTS)


def test_vector_error_marks_span_and_metric(telemetry, qdrant):
    qdrant.fake.fail = ConnectionError(f"http://qdrant:6333 {QUERY}")

    with pytest.raises(ConnectionError):
        asyncio.run(
            qdrant.client.search(collection_name=COLLECTION, vectors=[VECTOR], limit=3)
        )

    (span,) = telemetry.spans("rag.vector.search")
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] == "ConnectionError"
    assert span.attributes["canchat.outcome"] == "error"
    assert "canchat.rag.result_count" not in span.attributes
    assert_no_content(span)
    durations = telemetry.points(rag_obs.RAG_VECTOR_DURATION)
    key = labels(
        operation="search",
        provider="qdrant",
        outcome="error",
        **{"error.type": "ConnectionError"},
    )
    assert durations[key].count == 1


def test_swallowed_query_error_is_recorded(telemetry, qdrant):
    # query() logs and returns None, but the span and metric still see the error.
    qdrant.fake.fail = ConnectionError(f"http://qdrant:6333 {QUERY}")

    result = asyncio.run(
        qdrant.client.query(collection_name=COLLECTION, filter={"hash": "secret"})
    )

    assert result is None
    (span,) = telemetry.spans("rag.vector.query")
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "ConnectionError"
    assert span.attributes["canchat.outcome"] == "error"
    assert "canchat.rag.result_count" not in span.attributes
    assert_no_content(span)
    durations = telemetry.points(rag_obs.RAG_VECTOR_DURATION)
    key = labels(
        operation="query",
        provider="qdrant",
        outcome="error",
        **{"error.type": "ConnectionError"},
    )
    assert durations[key].count == 1
    assert labels(operation="query") not in telemetry.points(rag_obs.RAG_RESULTS)


@pytest.mark.parametrize(
    "args, kwargs, expected",
    [
        ((COLLECTION, ["a", "b"]), {}, 2),
        ((COLLECTION,), {"ids": ["a"]}, 1),
        ((COLLECTION,), {"ids": []}, 0),
        ((COLLECTION,), {"ids": None, "filter": {"file_id": "x"}}, None),
        ((COLLECTION,), {}, None),
    ],
)
def test_count_arg(args, kwargs, expected):
    assert rag_obs.count_arg("ids")(args, kwargs) == expected


def test_delete_by_filter_has_no_item_count(telemetry, qdrant):
    asyncio.run(
        qdrant.client.delete(collection_name=COLLECTION, filter={"file_id": "x"})
    )

    (span,) = telemetry.spans("rag.vector.delete")
    assert "canchat.rag.item_count" not in span.attributes
    assert span.attributes["canchat.outcome"] == "success"


def test_local_embedding_span_and_metric(telemetry, clock):
    model = FakeModel(clock)
    embed = retrieval_utils.get_embedding_function(
        "", "sentence-transformers/all-MiniLM-L6-v2", model, "", "", 8
    )

    assert embed(QUERY) == VECTOR
    assert embed([DOC, DOC, DOC]) == [VECTOR, VECTOR, VECTOR]

    single, batch = telemetry.spans("rag.embedding")
    assert single.attributes["canchat.rag.input_count"] == 1
    assert batch.attributes["canchat.rag.input_count"] == 3
    for span in (single, batch):
        assert span.attributes["canchat.rag.embedding.provider"] == (
            "sentence_transformers"
        )
        assert span.attributes["canchat.rag.embedding.model"] == (
            "sentence-transformers/all-MiniLM-L6-v2"
        )
        assert_no_content(span)
    point = telemetry.points(rag_obs.RAG_EMBEDDING_DURATION)[
        labels(
            provider="sentence_transformers",
            model="sentence-transformers/all-MiniLM-L6-v2",
            outcome="success",
        )
    ]
    assert point.count == 2
    assert point.sum == pytest.approx(0.5)


@pytest.mark.parametrize("query", [QUERY, [QUERY, DOC]], ids=["single", "batch"])
def test_remote_embedding_failure_is_error(telemetry, monkeypatch, query):
    # generate_*_batch_embeddings log and return None on provider errors.
    monkeypatch.setattr(
        retrieval_utils, "generate_openai_batch_embeddings", lambda *a, **kw: None
    )
    embed = retrieval_utils.get_embedding_function(
        "openai", "text-embedding-3-small", None, "http://x", "key", 8
    )

    with pytest.raises(retrieval_utils.EmbeddingFailed):
        embed(query)

    (span,) = telemetry.spans("rag.embedding")
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == rag_obs.EMBEDDING_FAILED
    assert span.attributes["canchat.rag.embedding.provider"] == "openai"
    assert_no_content(span)
    point = telemetry.points(rag_obs.RAG_EMBEDDING_DURATION)[
        labels(
            provider="openai",
            model="text-embedding-3-small",
            outcome="error",
            **{"error.type": rag_obs.EMBEDDING_FAILED},
        )
    ]
    assert point.count == 1


def test_query_collection_nests_embedding_and_search(telemetry, qdrant, clock):
    model = FakeModel(clock)
    embed = retrieval_utils.get_embedding_function("", "m", model, "", "", 8)
    qdrant.fake.delay = 0.5

    result = asyncio.run(
        retrieval_utils.query_collection(
            collection_names=[COLLECTION, "file-second"],
            queries=[QUERY],
            embedding_function=embed,
            k=3,
        )
    )

    assert model.calls == [QUERY]
    (retrieve,) = telemetry.spans("rag.retrieve")
    (embedding,) = telemetry.spans("rag.embedding")
    searches = telemetry.spans("rag.vector.search")
    assert len(searches) == 2
    for child in (embedding, *searches):
        assert child.parent.span_id == retrieve.context.span_id
        assert child.context.trace_id == retrieve.context.trace_id
    assert retrieve.attributes["canchat.rag.search_type"] == "vector"
    assert retrieve.attributes["canchat.rag.collections.count"] == 2
    assert retrieve.attributes["canchat.rag.queries.count"] == 1
    assert retrieve.attributes["canchat.rag.top_k"] == 3
    assert retrieve.attributes["canchat.rag.result_count"] == len(
        result["documents"][0]
    )
    for span in telemetry.spans():
        assert_no_content(span)

    point = telemetry.points(rag_obs.RAG_RETRIEVAL_DURATION)[
        labels(operation="vector", outcome="success")
    ]
    assert point.count == 1
    assert point.sum == pytest.approx(0.25 + 2 * 0.5)
    results = telemetry.points(rag_obs.RAG_RESULTS)
    assert results[labels(operation="retrieve")].count == 1


@pytest.mark.parametrize(
    "name, expected",
    [
        ("file-0b8a4d6e", "file"),
        ("web-search-abc123", "web-search"),
        ("knowledge-abc", "knowledge"),
        ("3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "knowledge"),
        ("private-project-name", "other"),
    ],
)
def test_collection_name_is_exported_as_type_only(telemetry, qdrant, name, expected):
    asyncio.run(qdrant.client.search(collection_name=name, vectors=[VECTOR], limit=1))

    (span,) = telemetry.spans("rag.vector.search")
    assert span.attributes["canchat.rag.collection.type"] == expected
    assert "db.collection.name" not in span.attributes
    assert all(name not in str(v) for v in span.attributes.values())


def test_query_collection_search_failure_records_error(telemetry, qdrant, clock):
    model = FakeModel(clock)
    embed = retrieval_utils.get_embedding_function("", "m", model, "", "", 8)
    qdrant.fake.fail = ConnectionError("simulated outage")

    result = asyncio.run(
        retrieval_utils.query_collection(
            collection_names=[COLLECTION],
            queries=[QUERY],
            embedding_function=embed,
            k=1,
        )
    )

    # The fallback still returns an empty result instead of raising.
    assert result["documents"] == [[]]
    (search,) = telemetry.spans("rag.vector.search")
    assert search.attributes["canchat.outcome"] == "error"
    (retrieve,) = telemetry.spans("rag.retrieve")
    assert retrieve.attributes["canchat.outcome"] == "error"
    assert retrieve.attributes["error.type"] == rag_obs.VECTOR_SEARCH_FAILED
    assert retrieve.status.status_code == StatusCode.ERROR
    assert_no_content(retrieve)
    point = telemetry.points(rag_obs.RAG_RETRIEVAL_DURATION)[
        labels(
            operation="vector",
            outcome="error",
            **{"error.type": rag_obs.VECTOR_SEARCH_FAILED},
        )
    ]
    assert point.count == 1


def test_hybrid_failure_records_error(telemetry, monkeypatch):
    async def failing(**kwargs):
        raise RuntimeError(QUERY)

    monkeypatch.setattr(retrieval_utils, "query_doc_with_hybrid_search", failing)

    with pytest.raises(Exception):
        asyncio.run(
            retrieval_utils.query_collection_with_hybrid_search(
                collection_names=[COLLECTION],
                queries=[QUERY],
                embedding_function=None,
                k=3,
                reranking_function=None,
                r=0.0,
            )
        )

    (span,) = telemetry.spans("rag.retrieve")
    assert span.attributes["canchat.rag.search_type"] == "hybrid"
    assert span.attributes["error.type"] == "Exception"
    assert_no_content(span)
    assert (
        telemetry.points(rag_obs.RAG_RETRIEVAL_DURATION)[
            labels(operation="hybrid", outcome="error", **{"error.type": "Exception"})
        ].count
        == 1
    )


def test_cancelled_vector_operation(telemetry, qdrant):
    qdrant.fake.fail = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(qdrant.client.get(collection_name=COLLECTION))

    (span,) = telemetry.spans("rag.vector.get")
    assert span.attributes["canchat.outcome"] == "cancelled"
    assert span.status.status_code == StatusCode.UNSET


def test_without_metrics_service_still_runs(monkeypatch, qdrant):
    monkeypatch.setattr(rag_obs, "get_metrics_service", lambda: None)
    result = asyncio.run(qdrant.client.get(collection_name=COLLECTION))
    assert rag_obs.count_ids(result) == 2
