"""OBS-31: embedding calls from async retrieval paths run off the event loop.

Each path gets an instrumented stub embedding function that records the
thread it ran on. Spans go to an in-memory exporter so the ``rag.embedding``
parent can be checked.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from open_webui.observability import rag as rag_obs
from open_webui.routers import retrieval as retrieval_router
from open_webui.test.util.test_rag_instrumentation import Telemetry

VECTOR = [0.125, 0.25]
OUTER_SPAN = "test.caller"


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


class RecordingEmbedding:
    """Blocking embedding stub; ``block`` is waited on before returning."""

    def __init__(self, block: threading.Event | None = None):
        self.threads: list[int] = []
        self.block = block
        self.unblocked: bool | None = None

    def __call__(self, query, user=None):
        self.threads.append(threading.get_ident())
        if self.block is not None:
            # Deadlocks (until timeout) if the caller blocks the event loop.
            self.unblocked = self.block.wait(timeout=5)
        if isinstance(query, list):
            return [VECTOR for _ in query]
        return VECTOR


class FakeVectorDB:
    def __init__(self):
        self.inserted = []
        self.collections = {"file-offloop"}

    async def has_collection(self, collection_name):
        return collection_name in self.collections

    async def insert(self, collection_name, items):
        self.inserted.extend(items)

    async def delete_collection(self, collection_name):
        pass


def make_request(embedding_function, *, hybrid=False):
    config = SimpleNamespace(
        RAG_EMBEDDING_ENGINE="",
        RAG_EMBEDDING_MODEL="stub-model",
        RAG_OPENAI_API_BASE_URL="",
        RAG_OPENAI_API_KEY="",
        RAG_OLLAMA_BASE_URL="",
        RAG_OLLAMA_API_KEY="",
        RAG_EMBEDDING_BATCH_SIZE=1,
        ENABLE_RAG_HYBRID_SEARCH=hybrid,
        TOP_K=3,
    )
    state = SimpleNamespace(
        config=config, ef=None, rf=None, EMBEDDING_FUNCTION=embedding_function
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def instrumented(stub):
    return rag_obs.instrument_embedding_function(
        stub, provider=rag_obs.LOCAL_EMBEDDING_PROVIDER, model="stub-model"
    )


@pytest.fixture
def vector_db(monkeypatch):
    fake = FakeVectorDB()
    monkeypatch.setattr(retrieval_router, "VECTOR_DB_CLIENT", fake)
    return fake


@pytest.fixture
def stub_get_embedding_function(monkeypatch):
    """Route ``save_docs_to_vector_db`` to the request's embedding function."""

    def factory(engine, model, ef, url, key, batch_size):
        return factory.func

    monkeypatch.setattr(retrieval_router, "get_embedding_function", factory)
    return factory


async def save_docs(request, user=None):
    return await retrieval_router.save_docs_to_vector_db(
        request,
        [Document(page_content="chunk one"), Document(page_content="chunk two")],
        "file-new-upload",
        split=False,
        user=user,
    )


async def query_doc(request):
    return await retrieval_router.query_doc_handler(
        request,
        retrieval_router.QueryDocForm(collection_name="file-offloop", query="q"),
        user=SimpleNamespace(id="u1"),
    )


async def ef_endpoint(request):
    if not hasattr(retrieval_router, "get_embeddings"):
        pytest.skip("GET /ef/{text} is only registered when ENV=dev")
    return await retrieval_router.get_embeddings(request, "hello")


def run_in_outer_span(telemetry, coro_factory):
    tracer = telemetry.bundle.tracer_provider.get_tracer("test")

    async def main():
        with tracer.start_as_current_span(OUTER_SPAN):
            return threading.get_ident(), await coro_factory()

    return asyncio.run(main())


@pytest.fixture
def query_doc_search(monkeypatch):
    calls = []

    async def fake_query_doc(collection_name, query_embedding, k):
        calls.append(query_embedding)
        return {"ids": [["a"]]}

    monkeypatch.setattr(retrieval_router, "query_doc", fake_query_doc)
    return calls


@pytest.mark.parametrize("path", ["save_docs", "query_doc", "ef_endpoint"])
def test_embedding_runs_off_loop_and_keeps_parent(
    path,
    telemetry,
    vector_db,
    stub_get_embedding_function,
    query_doc_search,
):
    stub = RecordingEmbedding()
    ef = instrumented(stub)
    stub_get_embedding_function.func = ef
    request = make_request(ef)
    call = {"save_docs": save_docs, "query_doc": query_doc, "ef_endpoint": ef_endpoint}[
        path
    ]

    loop_thread, result = run_in_outer_span(telemetry, lambda: call(request))

    assert stub.threads, "embedding function was not called"
    assert loop_thread not in stub.threads

    (outer,) = telemetry.spans(OUTER_SPAN)
    embeddings = telemetry.spans("rag.embedding")
    assert embeddings
    for span in embeddings:
        assert span.parent is not None
        assert span.parent.span_id == outer.context.span_id
        assert span.context.trace_id == outer.context.trace_id

    if path == "save_docs":
        assert result is True
        assert [item["vector"] for item in vector_db.inserted] == [VECTOR, VECTOR]
    elif path == "query_doc":
        assert result == {"ids": [["a"]]}
        assert query_doc_search == [VECTOR]
    else:
        assert result == {"result": VECTOR}


def test_save_docs_passes_user_to_embedding(vector_db, stub_get_embedding_function):
    seen = []

    def ef(query, user=None):
        seen.append(user)
        return [VECTOR for _ in query]

    stub_get_embedding_function.func = ef
    user = SimpleNamespace(id="u1")

    assert asyncio.run(save_docs(make_request(ef), user=user)) is True
    assert seen == [user]


@pytest.mark.parametrize("path", ["save_docs", "query_doc", "ef_endpoint"])
def test_slow_embedding_does_not_block_concurrent_request(
    path, vector_db, stub_get_embedding_function, query_doc_search
):
    # The embedding blocks until a concurrent coroutine releases it, which
    # only happens if the event loop keeps running during the embedding.
    release = threading.Event()
    stub = RecordingEmbedding(block=release)
    stub_get_embedding_function.func = stub
    request = make_request(stub)
    call = {"save_docs": save_docs, "query_doc": query_doc, "ef_endpoint": ef_endpoint}[
        path
    ]

    async def concurrent_request(started: asyncio.Event):
        await started.wait()
        await asyncio.sleep(0)
        release.set()
        return "pong"

    async def main():
        started = asyncio.Event()

        async def slow():
            started.set()
            return await call(request)

        return await asyncio.wait_for(
            asyncio.gather(slow(), concurrent_request(started)), timeout=10
        )

    _, pong = asyncio.run(main())

    assert pong == "pong"
    assert stub.unblocked is True
