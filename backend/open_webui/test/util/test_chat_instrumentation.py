"""OBS-22: chat payload, tool, file and web search spans and chat duration.

``main.chat_completion`` runs for real with the provider, search engine,
query generation and retrieval stubbed. Spans go through the production
``SanitizingSpanExporter`` into an in-memory exporter; the chat duration goes
through ``MetricsService`` (strict) into an in-memory metric reader.
"""

import asyncio
import gc
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from starlette.responses import StreamingResponse

import open_webui.main as main
from open_webui.config import DEFAULT_METRIC_DEFINITIONS
from open_webui.constants import TASKS
from open_webui.metrics.service import MetricsService
from open_webui.observability import chat as chat_obs
from open_webui.observability.providers import OTelSettings, create_providers
from open_webui.utils import middleware

MODEL_ID = "test-model"
CHAT_ID = "6f1c2f9e-0b8a-4d6e-9a43-2f6b8f0c1d22"
DURATION = chat_obs.CHAT_OPERATION_DURATION
PAYLOAD_CHILDREN = ["chat.web_search", "chat.filters", "chat.tools", "chat.files"]
# Every attribute a chat span may carry. Anything else (queries, file names,
# URLs, tool names) must never be set.
ALLOWED_CHAT_ATTRIBUTES = {
    "canchat.operation",
    "canchat.stream",
    "canchat.chat.id",
    "canchat.model",
    "canchat.outcome",
    "canchat.response.mode",
    "canchat.files.count",
    "canchat.tool_ids.count",
    "canchat.sources.count",
    "canchat.feature.web_search",
    "canchat.feature.wiki_grounding",
    "canchat.feature.image_generation",
    "error.type",
}


def otel_settings():
    return OTelSettings(
        enabled=False,
        service_name="test",
        service_version="test",
        environment="test",
        endpoint="http://collector:4318",
        meter_name="test",
        metric_export_interval_ms=15000,
        traces_sampler_ratio=1.0,
    )


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

    def spans(self):
        return {s.name: s for s in self.exporter.get_finished_spans()}

    def points(self):
        data = self.reader.get_metrics_data()
        for resource_metrics in data.resource_metrics if data else ():
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == DURATION:
                        return list(metric.data.data_points)
        return []


@pytest.fixture
def telemetry(monkeypatch):
    t = Telemetry()
    monkeypatch.setattr(
        chat_obs,
        "_tracer",
        lambda: t.bundle.tracer_provider.get_tracer(chat_obs.TRACER_NAME),
    )
    yield t
    t.bundle.shutdown()


class Chat:
    """Stubs for everything ``chat_completion`` reaches outside the process."""

    def __init__(self, monkeypatch, metrics, *, query="obs22 query", doc="doc"):
        self.events = []
        self.searched = []
        self.retrieved = []
        self.titles = []
        self.task = None
        self.query = query
        self.doc = doc

        async def emitter(event):
            self.events.append(event)

        async def generate_queries(request, form_data, user):
            return {
                "choices": [
                    {"message": {"content": json.dumps({"queries": [self.query]})}}
                ]
            }

        async def process_web_search(request, form_data, user=None):
            self.searched.append(form_data.query)
            return {
                "docs": [{"content": self.doc, "metadata": {"source": "web"}}],
                "filenames": [f"https://example.test/{self.query}"],
            }

        async def get_sources_from_files(request, files, queries, **kwargs):
            self.retrieved.append([f.get("name") for f in files])
            return [
                {
                    "source": {"name": f.get("name")},
                    "document": [self.doc],
                    "metadata": [{"source": f.get("name")}],
                }
                for f in files
            ]

        async def generate_title(request, form_data, user):
            self.titles.append(form_data["chat_id"])
            return {"choices": [{"message": {"content": "Title"}}]}

        def create_task(coroutine):
            self.task = asyncio.create_task(coroutine)
            return "task-id", self.task

        message_state = {"content": "", "model": MODEL_ID, "id": "message-1"}

        def upsert(_chat_id, _message_id, message):
            message_state.update(message)

        monkeypatch.setattr(middleware, "get_event_emitter", lambda _m: emitter)
        monkeypatch.setattr(middleware, "generate_queries", generate_queries)
        monkeypatch.setattr(middleware, "process_web_search", process_web_search)
        monkeypatch.setattr(
            middleware, "get_sources_from_files", get_sources_from_files
        )
        monkeypatch.setattr(middleware, "generate_title", generate_title)
        monkeypatch.setattr(middleware, "create_task", create_task)
        monkeypatch.setattr(middleware, "ENABLE_REALTIME_CHAT_SAVE", True)
        monkeypatch.setattr(middleware, "get_active_status_by_user_id", lambda _u: 1)
        monkeypatch.setattr(
            middleware.Functions, "get_global_filter_functions", lambda: []
        )
        monkeypatch.setattr(
            middleware.Functions, "get_functions_by_type", lambda *a, **k: []
        )
        monkeypatch.setattr(
            middleware.Chats, "upsert_message_to_chat_by_id_and_message_id", upsert
        )
        monkeypatch.setattr(
            middleware.Chats,
            "get_message_by_id_and_message_id",
            lambda *_: dict(message_state),
        )
        monkeypatch.setattr(
            middleware.Chats,
            "get_messages_by_chat_id",
            lambda _c: {"message-1": dict(message_state)},
        )
        monkeypatch.setattr(
            middleware, "get_message_list", lambda *_: [{"content": "q"}] * 2
        )
        monkeypatch.setattr(
            middleware.Chats, "update_chat_title_by_id", lambda *a, **k: None
        )
        monkeypatch.setattr(middleware.Chats, "get_chat_title_by_id", lambda _c: "T")
        monkeypatch.setattr(
            middleware.MessageMetrics, "insert_new_metrics", lambda *a, **k: None
        )

        config = SimpleNamespace(
            WEBUI_URL="http://localhost",
            TOP_K=3,
            RELEVANCE_THRESHOLD=0.0,
            ENABLE_RAG_HYBRID_SEARCH=False,
            RAG_FULL_CONTEXT=False,
            RAG_TEMPLATE="[context]{{CONTEXT}}[/context] {{QUERY}}",
            TASK_MODEL="",
            TASK_MODEL_EXTERNAL="",
        )
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={
                        MODEL_ID: {
                            "id": MODEL_ID,
                            "name": "Test",
                            "owned_by": "openai",
                            "context_length": 128000,
                            "info": {"meta": {}},
                        }
                    },
                    metrics_service=metrics,
                    config=config,
                    EMBEDDING_FUNCTION=None,
                    rf=None,
                )
            )
        )
        self.user = SimpleNamespace(
            id="user-1",
            email="user@example.test",
            name="User",
            role="admin",
            settings=None,
        )
        self._monkeypatch = monkeypatch

    def respond_with(self, response):
        async def handler(request, form_data, user):
            return response() if callable(response) else response

        self._monkeypatch.setattr(main, "chat_completion_handler", handler)

    def form(
        self,
        prompt="hello",
        *,
        stream=False,
        socket=False,
        file="report.pdf",
        chat_id=CHAT_ID,
    ):
        form = {
            "model": MODEL_ID,
            "stream": stream,
            "chat_id": chat_id,
            "id": "message-1",
            "messages": [{"role": "user", "content": prompt}],
            "features": {"web_search": True},
            "files": [{"type": "file", "id": "file-1", "name": file}],
        }
        if socket:
            form["session_id"] = "session-1"
            form["background_tasks"] = {TASKS.TITLE_GENERATION: True}
        return form

    async def complete(self, form):
        return await main.chat_completion(self.request, form, user=self.user)


def json_response():
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
    }


def sse_response(chunks=("hi",), fail_after=None, block=None, started=None):
    async def body():
        for index, text in enumerate(chunks):
            if fail_after is not None and index == fail_after:
                raise ConnectionError("provider dropped")
            yield f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n"
            if started is not None:
                started.set()
            if block is not None:
                await block.wait()
        yield "data: [DONE]\n\n"

    return lambda: StreamingResponse(body(), media_type="text/event-stream")


def assert_only_allowed_attributes(spans):
    for span in spans.values():
        assert set(span.attributes) <= ALLOWED_CHAT_ATTRIBUTES, span.name
        for event in span.events:
            assert set(event.attributes or {}) <= {"exception.type"}, span.name


def single_point(telemetry):
    points = telemetry.points()
    assert len(points) == 1
    assert points[0].count == 1
    return points[0]


@pytest.mark.anyio
async def test_span_tree_for_web_search_and_file(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(json_response)

    result = await chat.complete(chat.form())

    assert result["choices"][0]["message"]["content"] == "answer"
    assert chat.searched == ["obs22 query"]
    # The web search result and the uploaded file are both retrieved.
    assert sorted(chat.retrieved[0]) == ["obs22 query", "report.pdf"]

    spans = telemetry.spans()
    operation = spans["chat.operation"]
    payload = spans["chat.process_payload"]
    assert payload.parent.span_id == operation.context.span_id
    for name in PAYLOAD_CHILDREN:
        assert spans[name].parent.span_id == payload.context.span_id, name
        assert spans[name].context.trace_id == operation.context.trace_id
    assert "chat.wiki_grounding" not in spans
    assert "chat.image_generation" not in spans

    assert dict(operation.attributes) == {
        "canchat.operation": "chat",
        "canchat.stream": False,
        "canchat.chat.id": CHAT_ID,
        "canchat.model": MODEL_ID,
        "canchat.response.mode": "json",
        "canchat.outcome": "success",
        "canchat.feature.web_search": True,
        "canchat.feature.wiki_grounding": False,
        "canchat.feature.image_generation": False,
    }
    assert dict(payload.attributes) == {
        "canchat.model": MODEL_ID,
        "canchat.chat.id": CHAT_ID,
        "canchat.files.count": 1,
        "canchat.tool_ids.count": 0,
        "canchat.feature.web_search": True,
        "canchat.feature.wiki_grounding": False,
        "canchat.feature.image_generation": False,
    }
    assert dict(spans["chat.files"].attributes) == {
        "canchat.files.count": 2,
        "canchat.sources.count": 2,
    }
    assert dict(spans["chat.tools"].attributes) == {
        "canchat.tool_ids.count": 0,
        "canchat.sources.count": 0,
    }
    assert_only_allowed_attributes(spans)
    assert all(s.status.status_code != StatusCode.ERROR for s in spans.values())

    point = single_point(telemetry)
    assert dict(point.attributes) == {
        "model": MODEL_ID,
        "stream": False,
        "outcome": "success",
    }


@pytest.mark.anyio
async def test_direct_stream_finishes_after_last_chunk(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a", "b")))

    response = await chat.complete(chat.form(stream=True))

    assert isinstance(response, StreamingResponse)
    # Preprocessing is done, delivery is not.
    assert "chat.process_payload" in telemetry.spans()
    assert "chat.operation" not in telemetry.spans()
    assert telemetry.points() == []

    body = [chunk async for chunk in response.body_iterator]

    assert body[-1].startswith('data: {"sources"')
    operation = telemetry.spans()["chat.operation"]
    assert operation.attributes["canchat.response.mode"] == "stream"
    assert operation.attributes["canchat.outcome"] == "success"
    assert operation.end_time >= telemetry.spans()["chat.process_payload"].end_time
    assert dict(single_point(telemetry).attributes)["stream"] is True


@pytest.mark.anyio
async def test_direct_stream_failure_is_recorded_once(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a", "b"), fail_after=1))

    response = await chat.complete(chat.form(stream=True))
    with pytest.raises(ConnectionError):
        async for _ in response.body_iterator:
            pass

    operation = telemetry.spans()["chat.operation"]
    assert operation.status.status_code == StatusCode.ERROR
    assert operation.attributes["error.type"] == "ConnectionError"
    assert not operation.events
    attrs = dict(single_point(telemetry).attributes)
    assert attrs["outcome"] == "error"
    assert attrs["error.type"] == "ConnectionError"


@pytest.mark.anyio
async def test_direct_stream_closed_by_client_is_cancelled(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a", "b", "c")))

    response = await chat.complete(chat.form(stream=True))
    iterator = response.body_iterator
    await iterator.__anext__()
    await iterator.aclose()

    assert telemetry.spans()["chat.operation"].attributes["canchat.outcome"] == (
        "cancelled"
    )
    assert dict(single_point(telemetry).attributes)["outcome"] == "cancelled"


@pytest.mark.anyio
async def test_background_stream_finishes_before_title_generation(
    telemetry, monkeypatch
):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a", "b")))

    result = await chat.complete(chat.form(stream=True, socket=True))

    assert result == {"status": True, "task_id": "task-id"}
    # The HTTP handler has returned; the operation is still delivering.
    assert "chat.operation" not in telemetry.spans()
    await chat.task

    spans = telemetry.spans()
    operation = spans["chat.operation"]
    title = spans["chat.generate_title"]
    assert chat.titles == [CHAT_ID]
    assert operation.attributes["canchat.response.mode"] == "background"
    assert operation.attributes["canchat.outcome"] == "success"
    # Title generation is a separate operation, after delivery: a new trace
    # linked to the chat operation rather than a child of an ended span.
    assert dict(title.attributes) == {"canchat.operation": str(TASKS.TITLE_GENERATION)}
    assert title.parent is None
    assert [link.context.span_id for link in title.links] == [operation.context.span_id]
    assert title.start_time >= operation.end_time
    assert_only_allowed_attributes(spans)
    assert dict(single_point(telemetry).attributes)["outcome"] == "success"


@pytest.mark.anyio
async def test_background_json_finishes_before_title_generation(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(json_response)

    await chat.complete(chat.form(socket=True))

    spans = telemetry.spans()
    assert spans["chat.generate_title"].start_time >= spans["chat.operation"].end_time
    assert single_point(telemetry).count == 1


@pytest.mark.anyio
async def test_background_cancellation_is_recorded_once(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    never = asyncio.Event()
    started = asyncio.Event()
    chat.respond_with(sse_response(("a", "b"), block=never, started=started))

    await chat.complete(chat.form(stream=True, socket=True))
    await asyncio.wait_for(started.wait(), timeout=5)
    chat.task.cancel()
    await asyncio.gather(chat.task, return_exceptions=True)

    operation = telemetry.spans()["chat.operation"]
    assert operation.attributes["canchat.outcome"] == "cancelled"
    assert operation.status.status_code != StatusCode.ERROR
    assert "chat.generate_title" not in telemetry.spans()
    assert {"type": "task-cancelled"} in chat.events
    assert dict(single_point(telemetry).attributes)["outcome"] == "cancelled"


@pytest.mark.anyio
async def test_background_failure_is_logged_and_recorded_once(
    telemetry, monkeypatch, caplog
):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a", "b"), fail_after=1))

    with caplog.at_level("ERROR", logger=middleware.log.name):
        await chat.complete(chat.form(stream=True, socket=True))
        await asyncio.gather(chat.task, return_exceptions=True)

    failures = [
        r for r in caplog.records if r.message == "Chat response background task failed"
    ]
    assert len(failures) == 1
    assert "ConnectionError" in failures[0].exc_text
    assert "provider dropped" not in failures[0].exc_text
    operation = telemetry.spans()["chat.operation"]
    assert operation.attributes["canchat.outcome"] == "error"
    assert operation.attributes["error.type"] == "ConnectionError"
    assert dict(single_point(telemetry).attributes)["outcome"] == "error"


@pytest.mark.anyio
async def test_unknown_model_records_error_without_model_label(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    form = chat.form()
    form["model"] = "free text that is not a model"

    with pytest.raises(HTTPException):
        await chat.complete(form)

    spans = telemetry.spans()
    assert "chat.process_payload" not in spans
    operation = spans["chat.operation"]
    assert "canchat.model" not in operation.attributes
    assert operation.attributes["error.type"] == "Exception"
    assert dict(single_point(telemetry).attributes) == {
        "model": "unknown",
        "stream": False,
        "outcome": "error",
        "error.type": "Exception",
    }


@pytest.mark.anyio
async def test_provider_error_records_error_once(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)

    def fail():
        raise ConnectionError("provider down")

    chat.respond_with(fail)

    with pytest.raises(HTTPException):
        await chat.complete(chat.form())

    assert telemetry.spans()["chat.operation"].attributes["canchat.outcome"] == (
        "error"
    )
    assert dict(single_point(telemetry).attributes)["error.type"] == ("ConnectionError")


@pytest.mark.anyio
async def test_swallowed_file_handler_error_marks_span(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(json_response)

    async def failing_files_handler(request, body, user):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(
        middleware, "chat_completion_files_handler", failing_files_handler
    )

    await chat.complete(chat.form())

    files = telemetry.spans()["chat.files"]
    assert files.status.status_code == StatusCode.ERROR
    assert files.attributes["error.type"] == "RuntimeError"
    # The chat still completes.
    assert dict(single_point(telemetry).attributes)["outcome"] == "success"


@pytest.mark.anyio
async def test_unread_direct_stream_finishes_when_collected(telemetry, monkeypatch):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(sse_response(("a",)))

    response = await chat.complete(chat.form(stream=True))
    assert "chat.operation" not in telemetry.spans()

    # Client gone before the first chunk: the body is never read.
    del response
    gc.collect()

    operation = telemetry.spans()["chat.operation"]
    assert operation.attributes["canchat.outcome"] == "cancelled"
    assert dict(single_point(telemetry).attributes)["outcome"] == "cancelled"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "chat_id, recorded",
    [
        ("my secret prompt text", None),
        ("local", "local"),
        (CHAT_ID.upper(), CHAT_ID),
    ],
)
async def test_chat_id_is_recorded_only_if_well_formed(
    telemetry, monkeypatch, chat_id, recorded
):
    chat = Chat(monkeypatch, telemetry.metrics)
    chat.respond_with(json_response)

    await chat.complete(chat.form(chat_id=chat_id))

    spans = telemetry.spans()
    for name in ["chat.operation", "chat.process_payload"]:
        assert spans[name].attributes.get("canchat.chat.id") == recorded, name


def test_finish_is_idempotent(telemetry):
    operation = chat_obs.ChatOperation(
        telemetry.bundle.tracer.start_span("chat.operation"), telemetry.metrics
    )
    operation.finish()
    operation.finish("error", RuntimeError())
    operation.finish("cancelled")

    assert dict(single_point(telemetry).attributes)["outcome"] == "success"


def test_chat_path_keeps_canaries_out_of_every_sink(canary_harness, monkeypatch):
    """Canary run (OBS-17) over the chat path, on the global tracer provider."""
    canaries = canary_harness.canaries
    reader = InMemoryMetricReader()
    bundle = create_providers(otel_settings(), metric_readers=[reader])
    metrics = MetricsService(bundle.meter, DEFAULT_METRIC_DEFINITIONS, strict=True)
    chat = Chat(monkeypatch, metrics, query=canaries.prompt, doc=canaries.prompt)

    async def run():
        chat.respond_with(sse_response((canaries.prompt,)))
        await chat.complete(
            chat.form(
                canaries.prompt,
                stream=True,
                socket=True,
                file=canaries.prompt,
                chat_id=canaries.prompt,
            )
        )
        await chat.task

    try:
        asyncio.run(run())
    finally:
        bundle.shutdown()

    assert chat.searched == [canaries.prompt]
    spans = canary_harness.sinks()["spans"]
    for name in ["chat.operation", "chat.process_payload", *PAYLOAD_CHILDREN]:
        assert f'"name": "{name}"' in spans, name
    canary_harness.assert_no_canaries()
