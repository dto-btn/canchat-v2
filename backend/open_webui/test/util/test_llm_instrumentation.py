"""OBS-23: ``llm.chat`` provider operation across every response mode.

``main.chat_completion`` runs for real through ``utils.chat`` and the OpenAI
router; only ``aiohttp`` (the provider), retrieval and persistence are stubbed
(see ``test_chat_instrumentation.Chat``). A fake monotonic clock, advanced by
the stub provider, makes durations and time to first token exact.
"""

import asyncio
import gc
import json
import logging
import time

import aiohttp
import pytest
from fastapi import HTTPException
from opentelemetry.trace import StatusCode
from starlette.responses import StreamingResponse

from open_webui.observability import chat as chat_obs
from open_webui.observability import llm as llm_obs
from open_webui.observability import tracing
from open_webui import functions as functions_module
from open_webui.routers import ollama as ollama_router
from open_webui.routers import openai as openai_router
from open_webui.test.util.test_chat_instrumentation import (
    CHAT_ID,
    MODEL_ID,
    Chat,
    Telemetry,
)
from open_webui.utils import middleware

ARENA_ID = "arena-model"
HEADER_DELAY = 1.0
# Time to the first content token in ``STREAM``: headers, then keepalives and
# a role-only delta that must not count as the first token.
EXPECTED_TTFT = HEADER_DELAY + 2.0 + 0.5
EXPECTED_DURATION = EXPECTED_TTFT + 1.0
ALLOWED_LLM_ATTRIBUTES = {
    "canchat.operation",
    "canchat.llm.provider",
    "canchat.llm.task",
    "canchat.model",
    "canchat.stream",
    "canchat.chat.id",
    "canchat.outcome",
    "canchat.llm.attempts",
    "canchat.llm.attempt",
    "canchat.llm.usage.known",
    "canchat.llm.usage.input_tokens",
    "canchat.llm.usage.output_tokens",
    "canchat.llm.time_to_first_token",
    "canchat.llm.pipeline",
    "error.type",
    *llm_obs.GEN_AI_ATTRIBUTES,
}


def sse(data):
    return f"data: {json.dumps(data)}\n".encode()


def delta(**fields):
    return sse({"model": MODEL_ID, "choices": [{"index": 0, "delta": fields}]})


USAGE = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
STREAM = [
    b": keepalive\n",
    b"\n",
    2.0,
    b": keepalive\n",
    delta(role="assistant"),
    0.5,
    delta(content="Hel"),
    b"\n",
    1.0,
    delta(content="lo"),
    sse({"model": MODEL_ID, "choices": [], "usage": USAGE}),
    b"data: [DONE]\n",
]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Provider:
    """Stub ``aiohttp.ClientSession`` for the provider.

    ``body`` items are bytes lines, or floats that advance the clock.
    """

    def __init__(self, monkeypatch, clock):
        self.clock = clock
        self.calls = 0
        self.body = list(STREAM)
        self.json = None
        self.status = 200
        self.fail_before_headers = False
        self.fail_at = None
        self.block = None
        self.started = asyncio.Event()
        provider = self

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            async def request(self, method, url, data=None, headers=None):
                return await provider.respond()

            async def post(self, url, data=None, headers=None):
                return await provider.respond()

            async def close(self):
                pass

        monkeypatch.setattr(aiohttp, "ClientSession", Session)

    async def respond(self):
        self.calls += 1
        self.clock.now += HEADER_DELAY
        if self.fail_before_headers:
            raise aiohttp.ClientConnectionError("connection refused")
        return Response(self)


class Response:
    def __init__(self, provider):
        self._provider = provider
        self.status = provider.status
        content_type = (
            "application/json" if provider.json is not None else "text/event-stream"
        )
        self.headers = {"Content-Type": content_type}
        self.content = self._content()

    async def _content(self):
        provider = self._provider
        for index, item in enumerate(provider.body):
            if provider.fail_at == index:
                raise aiohttp.ClientPayloadError("provider dropped")
            if isinstance(item, float):
                provider.clock.now += item
                continue
            yield item
            provider.started.set()
            if provider.block is not None:
                await provider.block.wait()

    async def json(self):
        return self._provider.json

    async def text(self):
        return json.dumps(self._provider.json)

    def raise_for_status(self):
        pass

    def close(self):
        pass


class LLMChat(Chat):
    """``Chat`` with the real provider path (utils.chat -> OpenAI router)."""

    def __init__(self, monkeypatch, telemetry):
        super().__init__(monkeypatch, telemetry.metrics)
        self.clock = Clock()
        monkeypatch.setattr(llm_obs, "_monotonic", self.clock)
        self.provider = Provider(monkeypatch, self.clock)
        self.handoff_ns = None

        state = self.request.app.state
        state.OPENAI_MODELS = {MODEL_ID: {"id": MODEL_ID, "urlIdx": 0}}
        state.MODELS[ARENA_ID] = {
            "id": ARENA_ID,
            "name": "Arena",
            "owned_by": "arena",
            "info": {"meta": {"model_ids": [MODEL_ID]}},
        }
        state.config.OPENAI_API_CONFIGS = {}
        state.config.OPENAI_API_BASE_URLS = ["http://provider.test/v1"]
        state.config.OPENAI_API_KEYS = ["test-key"]

        async def no_models(request):
            return {}

        monkeypatch.setattr(openai_router, "get_all_models", no_models)
        monkeypatch.setattr(openai_router.Models, "get_model_by_id", lambda _id: None)

        create_task = middleware.create_task

        def record_handoff(coroutine):
            self.handoff_ns = time.time_ns()
            return create_task(coroutine)

        monkeypatch.setattr(middleware, "create_task", record_handoff)


@pytest.fixture
def telemetry(monkeypatch):
    t = Telemetry()

    def tracer_for(name):
        return lambda: t.bundle.tracer_provider.get_tracer(name)

    monkeypatch.setattr(chat_obs, "_tracer", tracer_for(chat_obs.TRACER_NAME))
    monkeypatch.setattr(llm_obs, "_tracer", tracer_for(llm_obs.TRACER_NAME))
    yield t
    t.bundle.shutdown()


@pytest.fixture
def chat(monkeypatch, telemetry):
    return LLMChat(monkeypatch, telemetry)


def spans_named(telemetry, name):
    return [s for s in telemetry.exporter.get_finished_spans() if s.name == name]


def only_span(telemetry, name):
    spans = spans_named(telemetry, name)
    assert len(spans) == 1, f"{name}: {len(spans)}"
    return spans[0]


def metric_points(telemetry, name):
    data = telemetry.reader.get_metrics_data()
    for resource_metrics in data.resource_metrics if data else ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return list(metric.data.data_points)
    return []


def assert_recorded_once(
    telemetry,
    outcome,
    *,
    stream,
    ttft=None,
    task="generation",
    provider="openai",
    model=MODEL_ID,
):
    """One request, one duration and (streams only) one TTFT value."""
    labels = {"model": model, "provider": provider}
    outcome_labels = {**labels, "task": task, "outcome": outcome}
    requests = metric_points(telemetry, llm_obs.LLM_REQUESTS)
    assert [(dict(p.attributes), p.value) for p in requests] == [(outcome_labels, 1)]
    durations = metric_points(telemetry, llm_obs.LLM_REQUEST_DURATION)
    assert [(dict(p.attributes), p.count) for p in durations] == [
        ({**outcome_labels, "stream": stream}, 1)
    ]
    ttfts = metric_points(telemetry, llm_obs.LLM_TIME_TO_FIRST_TOKEN)
    if ttft is None:
        assert ttfts == []
    else:
        assert [(dict(p.attributes), p.count) for p in ttfts] == [(labels, 1)]
        assert ttfts[0].sum == pytest.approx(ttft)
    return durations[0]


def assert_llm_span_tree(telemetry):
    """``llm.chat`` is a child of ``chat.operation``; attempts nest below it."""
    operation = only_span(telemetry, "chat.operation")
    llm = only_span(telemetry, "llm.chat")
    assert llm.parent.span_id == operation.context.span_id
    assert llm.context.trace_id == operation.context.trace_id
    for attempt in spans_named(telemetry, "llm.attempt"):
        assert attempt.parent.span_id == llm.context.span_id
    for span in [llm, *spans_named(telemetry, "llm.attempt")]:
        assert set(span.attributes) <= ALLOWED_LLM_ATTRIBUTES, span.name
        # No exception events, no per-chunk events.
        assert not span.events, span.name
    # The provider call starts after payload processing, not with the request.
    assert llm.start_time >= only_span(telemetry, "chat.process_payload").end_time
    return llm


@pytest.mark.anyio
async def test_background_stream(telemetry, chat, caplog):
    caplog.set_level(logging.INFO)
    result = await chat.complete(chat.form(stream=True, socket=True))

    assert result == {"status": True, "task_id": "task-id"}
    # The HTTP handler has returned, the provider stream is still being read:
    # llm.chat outlives the handler.
    assert spans_named(telemetry, "llm.chat") == []
    await chat.task

    llm = assert_llm_span_tree(telemetry)
    assert llm.start_time < chat.handoff_ns
    assert llm.end_time <= only_span(telemetry, "chat.operation").end_time
    assert dict(llm.attributes) == {
        "canchat.operation": "llm.chat",
        "canchat.llm.provider": "openai",
        "canchat.llm.task": "generation",
        "canchat.model": MODEL_ID,
        "canchat.stream": True,
        "canchat.chat.id": CHAT_ID,
        "canchat.outcome": "success",
        "canchat.llm.attempts": 1,
        "canchat.llm.usage.known": True,
        "canchat.llm.usage.input_tokens": 7,
        "canchat.llm.usage.output_tokens": 2,
        "canchat.llm.time_to_first_token": pytest.approx(EXPECTED_TTFT),
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL_ID,
        "gen_ai.response.model": MODEL_ID,
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    assert dict(only_span(telemetry, "llm.attempt").attributes) == {
        "canchat.llm.attempt": 1
    }
    duration = assert_recorded_once(
        telemetry, "success", stream=True, ttft=EXPECTED_TTFT
    )
    assert duration.sum == pytest.approx(EXPECTED_DURATION)
    assert not [r for r in caplog.records if r.name == llm_obs.__name__]


@pytest.mark.anyio
async def test_direct_http_stream(telemetry, chat):
    response = await chat.complete(chat.form(stream=True))
    handoff_ns = time.time_ns()

    assert isinstance(response, StreamingResponse)
    assert spans_named(telemetry, "llm.chat") == []
    body = b"".join(
        [
            c if isinstance(c, bytes) else c.encode()
            async for c in response.body_iterator
        ]
    )

    assert b"data: [DONE]" in body
    llm = assert_llm_span_tree(telemetry)
    assert llm.start_time < handoff_ns
    assert llm.attributes["canchat.outcome"] == "success"
    assert_recorded_once(telemetry, "success", stream=True, ttft=EXPECTED_TTFT)


@pytest.mark.anyio
async def test_ttft_excludes_keepalives_and_headers(telemetry, chat):
    # Same content timing without any keepalive or role-only line.
    chat.provider.body = [2.5, delta(content="Hel"), 1.0, b"data: [DONE]\n"]

    response = await chat.complete(chat.form(stream=True))
    [_ async for _ in response.body_iterator]

    assert_recorded_once(telemetry, "success", stream=True, ttft=EXPECTED_TTFT)


@pytest.mark.anyio
async def test_json_response_has_no_ttft(telemetry, chat):
    chat.provider.json = {
        "model": MODEL_ID,
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
        "usage": USAGE,
    }

    result = await chat.complete(chat.form())

    assert result["choices"][0]["message"]["content"] == "answer"
    llm = assert_llm_span_tree(telemetry)
    assert llm.attributes["canchat.stream"] is False
    assert llm.attributes["canchat.llm.usage.input_tokens"] == 7
    assert "canchat.llm.time_to_first_token" not in llm.attributes
    duration = assert_recorded_once(telemetry, "success", stream=False)
    assert duration.sum == pytest.approx(HEADER_DELAY)


@pytest.mark.anyio
async def test_missing_usage_is_unknown_not_zero(telemetry, chat):
    chat.provider.body = [delta(content="hi"), b"data: [DONE]\n"]

    response = await chat.complete(chat.form(stream=True))
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.llm.usage.known"] is False
    assert "canchat.llm.usage.input_tokens" not in llm.attributes
    assert "canchat.llm.usage.output_tokens" not in llm.attributes
    assert "gen_ai.usage.input_tokens" not in llm.attributes
    assert "gen_ai.usage.output_tokens" not in llm.attributes


@pytest.mark.anyio
async def test_failure_before_headers(telemetry, chat):
    chat.provider.fail_before_headers = True

    with pytest.raises(HTTPException):
        await chat.complete(chat.form(stream=True, socket=True))

    llm = assert_llm_span_tree(telemetry)
    attempt = only_span(telemetry, "llm.attempt")
    for span in (llm, attempt):
        assert span.status.status_code == StatusCode.ERROR
        assert span.attributes["error.type"] == "ClientConnectionError"
    assert llm.attributes["canchat.outcome"] == "error"
    assert "canchat.llm.time_to_first_token" not in llm.attributes
    assert_recorded_once(telemetry, "error", stream=True)
    assert only_span(telemetry, "chat.operation").attributes["canchat.outcome"] == (
        "error"
    )


@pytest.mark.anyio
async def test_mid_stream_failure(telemetry, chat):
    chat.provider.fail_at = STREAM.index(b"data: [DONE]\n")

    response = await chat.complete(chat.form(stream=True))
    with pytest.raises(aiohttp.ClientPayloadError):
        async for _ in response.body_iterator:
            pass

    llm = assert_llm_span_tree(telemetry)
    assert llm.status.status_code == StatusCode.ERROR
    assert llm.attributes["error.type"] == "ClientPayloadError"
    # The first token arrived before the failure.
    assert_recorded_once(telemetry, "error", stream=True, ttft=EXPECTED_TTFT)


@pytest.mark.anyio
async def test_background_cancellation(telemetry, chat):
    chat.provider.block = asyncio.Event()

    await chat.complete(chat.form(stream=True, socket=True))
    await asyncio.wait_for(chat.provider.started.wait(), timeout=5)
    chat.task.cancel()
    await asyncio.gather(chat.task, return_exceptions=True)

    llm = assert_llm_span_tree(telemetry)
    assert llm.attributes["canchat.outcome"] == "cancelled"
    assert llm.status.status_code != StatusCode.ERROR
    assert_recorded_once(telemetry, "cancelled", stream=True)


@pytest.mark.anyio
async def test_direct_stream_closed_by_client(telemetry, chat):
    response = await chat.complete(chat.form(stream=True))
    iterator = response.body_iterator
    await iterator.__anext__()
    # The client goes away: the provider stream is never read to the end.
    await iterator.aclose()

    llm = assert_llm_span_tree(telemetry)
    assert llm.attributes["canchat.outcome"] == "cancelled"
    assert_recorded_once(telemetry, "cancelled", stream=True)


@pytest.mark.anyio
async def test_unread_direct_stream(telemetry, chat):
    response = await chat.complete(chat.form(stream=True))

    del response
    gc.collect()

    assert only_span(telemetry, "llm.chat").attributes["canchat.outcome"] == (
        "cancelled"
    )
    assert_recorded_once(telemetry, "cancelled", stream=True)


@pytest.mark.anyio
async def test_multi_model_request_is_one_provider_operation(telemetry, chat):
    form = chat.form(stream=True, socket=True)
    form["model"] = ARENA_ID

    await chat.complete(form)
    await chat.task

    # The arena picks a model and calls itself again; only the provider call
    # is an LLM operation, and its injected ``selected_model_id`` event is not
    # a token.
    llm = assert_llm_span_tree(telemetry)
    assert llm.attributes["canchat.model"] == MODEL_ID
    assert chat.provider.calls == 1
    assert_recorded_once(telemetry, "success", stream=True, ttft=EXPECTED_TTFT)


def test_retries_have_attempt_spans_and_count_once(telemetry, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(llm_obs, "_monotonic", clock)
    request = type("R", (), {})()
    request.app = type("A", (), {})()
    request.app.state = type("S", (), {"metrics_service": telemetry.metrics})()

    operation = llm_obs.LLMOperation.start(
        request, provider="openai", model=MODEL_ID, stream=False
    )
    with pytest.raises(aiohttp.ClientConnectionError):
        with operation.attempt():
            raise aiohttp.ClientConnectionError()
    with operation.attempt():
        clock.now += 2.0
    operation.finish()
    operation.finish("error", RuntimeError())

    attempts = sorted(
        spans_named(telemetry, "llm.attempt"),
        key=lambda s: s.attributes["canchat.llm.attempt"],
    )
    assert [a.status.status_code for a in attempts] == [
        StatusCode.ERROR,
        StatusCode.UNSET,
    ]
    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.llm.attempts"] == 2
    assert llm.attributes["canchat.outcome"] == "success"
    duration = assert_recorded_once(telemetry, "success", stream=False)
    assert duration.sum == pytest.approx(2.0)


@pytest.mark.anyio
async def test_ollama_ndjson_stream(telemetry, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(llm_obs, "_monotonic", clock)
    provider = Provider(monkeypatch, clock)

    def line(data):
        return (json.dumps(data) + "\n").encode()

    provider.body = [
        2.0,
        line({"model": "m", "message": {"role": "assistant", "content": ""}}),
        0.5,
        line({"model": "m", "message": {"content": "hi"}, "done": False}),
        line(
            {
                "model": "m",
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": 11,
                "eval_count": 3,
            }
        ),
    ]
    request = type("R", (), {})()
    request.app = type("A", (), {})()
    request.app.state = type("S", (), {"metrics_service": telemetry.metrics})()
    operation = llm_obs.LLMOperation.start(
        request, provider="ollama", model=MODEL_ID, stream=True
    )

    response = await ollama_router.send_post_request(
        "http://ollama.test/api/chat", "{}", stream=True, llm=operation
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.llm.usage.input_tokens"] == 11
    assert llm.attributes["canchat.llm.usage.output_tokens"] == 3
    ttfts = metric_points(telemetry, llm_obs.LLM_TIME_TO_FIRST_TOKEN)
    assert [dict(p.attributes) for p in ttfts] == [
        {"model": MODEL_ID, "provider": "ollama"}
    ]
    assert ttfts[0].sum == pytest.approx(HEADER_DELAY + 2.5)


def fake_request(metrics, **state):
    request = type("R", (), {})()
    request.app = type("A", (), {})()
    request.app.state = type("S", (), {"metrics_service": metrics, **state})()
    return request


@pytest.mark.parametrize(
    "metadata, expected",
    [
        (None, "generation"),
        ({"task": "title_generation"}, "title_generation"),
        ({"task": "function_calling"}, "function_calling"),
        ({"task": "user-supplied value"}, "other"),
    ],
)
def test_task_model_calls_are_labelled(telemetry, metadata, expected):
    operation = llm_obs.LLMOperation.start(
        fake_request(telemetry.metrics),
        provider="openai",
        model=MODEL_ID,
        stream=False,
        metadata=metadata,
    )
    operation.finish()

    assert only_span(telemetry, "llm.chat").attributes["canchat.llm.task"] == expected
    assert_recorded_once(telemetry, "success", stream=False, task=expected)


@pytest.mark.anyio
async def test_observation_failure_never_breaks_the_stream(
    telemetry, chat, monkeypatch
):
    def broken(self, chunk, fmt):
        raise RuntimeError("telemetry bug")

    monkeypatch.setattr(llm_obs.LLMOperation, "_observe_chunk", broken)

    response = await chat.complete(chat.form(stream=True))
    received = [chunk async for chunk in response.body_iterator]

    # Every provider line still reaches the client (the middleware's own
    # ``sources`` event is a str).
    provider_lines = [item for item in STREAM if isinstance(item, bytes)]
    assert [c for c in received if isinstance(c, bytes)] == provider_lines
    assert only_span(telemetry, "llm.chat").attributes["canchat.outcome"] == ("success")
    assert_recorded_once(telemetry, "success", stream=True)


@pytest.mark.anyio
async def test_start_failure_is_fail_open(telemetry, chat, monkeypatch):
    def broken_tracer():
        raise RuntimeError("tracer unavailable")

    monkeypatch.setattr(llm_obs, "_tracer", broken_tracer)
    chat.provider.json = {
        "model": MODEL_ID,
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
    }

    result = await chat.complete(chat.form())

    assert result["choices"][0]["message"]["content"] == "answer"
    assert chat.provider.calls == 1
    assert spans_named(telemetry, "llm.chat") == []
    assert metric_points(telemetry, llm_obs.LLM_REQUESTS) == []


@pytest.mark.anyio
async def test_error_status_stream_is_an_error(telemetry, chat):
    chat.provider.status = 429
    chat.provider.body = [sse({"error": {"message": "rate limited"}})]

    response = await chat.complete(chat.form(stream=True))
    received = [chunk async for chunk in response.body_iterator]

    # The response itself is unchanged: still streamed to the client.
    assert [c for c in received if isinstance(c, bytes)] == chat.provider.body
    llm = only_span(telemetry, "llm.chat")
    assert llm.status.status_code == StatusCode.ERROR
    assert llm.attributes["error.type"] == "429"
    assert_recorded_once(telemetry, "error", stream=True)


@pytest.mark.anyio
async def test_in_band_provider_error_is_an_error(telemetry, chat):
    chat.provider.body = [
        2.5,
        delta(content="Hel"),
        sse({"error": {"message": "upstream overloaded"}}),
        b"data: [DONE]\n",
    ]

    response = await chat.complete(chat.form(stream=True))
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.status.status_code == StatusCode.ERROR
    assert llm.attributes["error.type"] == "provider_error"
    assert_recorded_once(telemetry, "error", stream=True, ttft=EXPECTED_TTFT)


OLLAMA_MODEL = f"{MODEL_ID}:latest"


def ollama_request(telemetry):
    config = type("C", (), {})()
    config.OLLAMA_BASE_URLS = ["http://ollama.test"]
    config.OLLAMA_API_CONFIGS = {}
    return fake_request(
        telemetry.metrics,
        config=config,
        OLLAMA_MODELS={OLLAMA_MODEL: {"urls": [0]}},
    )


@pytest.mark.anyio
async def test_ollama_generate_endpoint(telemetry, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(llm_obs, "_monotonic", clock)
    provider = Provider(monkeypatch, clock)
    provider.body = [
        2.0,
        b'{"model": "m", "response": "", "done": false}\n',
        0.5,
        b'{"model": "m", "response": "hi", "done": false}\n',
        b'{"model": "m", "response": "", "done": true, '
        b'"prompt_eval_count": 4, "eval_count": 1}\n',
    ]

    response = await ollama_router.generate_completion(
        ollama_request(telemetry),
        ollama_router.GenerateCompletionForm(model=MODEL_ID, prompt="p"),
        url_idx=0,
        user=None,
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.llm.usage.output_tokens"] == 1
    assert_recorded_once(
        telemetry,
        "success",
        stream=True,
        ttft=HEADER_DELAY + 2.5,
        provider="ollama",
        model=OLLAMA_MODEL,
    )


@pytest.mark.anyio
async def test_ollama_openai_compatible_endpoint(telemetry, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(llm_obs, "_monotonic", clock)
    provider = Provider(monkeypatch, clock)
    monkeypatch.setattr(ollama_router.Models, "get_model_by_id", lambda _id: None)

    async def ollama_url(request, model, url_idx):
        return "http://ollama.test", 0

    monkeypatch.setattr(ollama_router, "get_ollama_url", ollama_url)
    admin = type("U", (), {"role": "admin", "id": "u"})()

    response = await ollama_router.generate_openai_chat_completion(
        ollama_request(telemetry),
        {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "metadata": {"task": "tags_generation"},
        },
        url_idx=0,
        user=admin,
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.llm.usage.input_tokens"] == 7
    assert_recorded_once(
        telemetry,
        "success",
        stream=True,
        ttft=EXPECTED_TTFT,
        task="tags_generation",
        provider="ollama",
        model=OLLAMA_MODEL,
    )


# OBS-25: approved ``gen_ai.*`` metadata on ``llm.chat``

PROMPT_SENTINEL = "obs25-secret-prompt"
COMPLETION_SENTINEL = "obs25-secret-completion"
UPSTREAM_MODEL = "gpt-4o-2024-08-06"


def gen_ai(span):
    return {k: v for k, v in span.attributes.items() if k.startswith("gen_ai.")}


def assert_no_content(span, *sentinels):
    assert set(span.attributes) <= ALLOWED_LLM_ATTRIBUTES
    for value in span.attributes.values():
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            for sentinel in sentinels:
                assert sentinel not in str(item)


def test_approved_gen_ai_attributes_survive_export_and_content_is_dropped():
    assert llm_obs.GEN_AI_ATTRIBUTES <= tracing.ALLOWED_SPAN_ATTRIBUTES
    allowed_gen_ai = {
        k for k in tracing.ALLOWED_SPAN_ATTRIBUTES if k.startswith("gen_ai.")
    }
    assert allowed_gen_ai == llm_obs.GEN_AI_ATTRIBUTES

    approved = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL_ID,
        "gen_ai.response.model": UPSTREAM_MODEL,
        "gen_ai.response.finish_reasons": ("stop",),
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    content = {
        "gen_ai.input.messages": PROMPT_SENTINEL,
        "gen_ai.output.messages": COMPLETION_SENTINEL,
        "gen_ai.system_instructions": PROMPT_SENTINEL,
        "gen_ai.prompt": PROMPT_SENTINEL,
        "gen_ai.completion": COMPLETION_SENTINEL,
        "gen_ai.tool.call.arguments": PROMPT_SENTINEL,
        "gen_ai.tool.call.result": COMPLETION_SENTINEL,
        "gen_ai.conversation.id": CHAT_ID,
        "gen_ai.system": "openai",
    }

    assert tracing.filter_span_attributes({**approved, **content}) == approved


@pytest.mark.anyio
async def test_openai_json_gen_ai_attributes_without_content(telemetry, chat):
    chat.provider.json = {
        "id": "chatcmpl-1",
        "model": UPSTREAM_MODEL,
        "choices": [
            {
                "message": {"role": "assistant", "content": COMPLETION_SENTINEL},
                "finish_reason": "stop",
            }
        ],
        "usage": USAGE,
    }

    result = await chat.complete(chat.form(PROMPT_SENTINEL))

    assert result["choices"][0]["message"]["content"] == COMPLETION_SENTINEL
    llm = assert_llm_span_tree(telemetry)
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL_ID,
        "gen_ai.response.model": UPSTREAM_MODEL,
        "gen_ai.response.finish_reasons": ("stop",),
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    assert "canchat.llm.pipeline" not in llm.attributes
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL, "chatcmpl-1")


@pytest.mark.anyio
async def test_openai_stream_finish_reason_after_first_token(telemetry, chat):
    def chunk(finish_reason, **fields):
        return sse(
            {
                "model": UPSTREAM_MODEL,
                "choices": [
                    {"index": 0, "delta": fields, "finish_reason": finish_reason}
                ],
            }
        )

    chat.provider.body = [
        chunk(None, role="assistant"),
        chunk(None, content=COMPLETION_SENTINEL),
        chunk(None, content="more"),
        chunk("length"),
        b"data: [DONE]\n",
    ]

    response = await chat.complete(chat.form(PROMPT_SENTINEL, stream=True))
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["gen_ai.response.model"] == UPSTREAM_MODEL
    assert llm.attributes["gen_ai.response.finish_reasons"] == ("length",)
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL)


@pytest.mark.anyio
async def test_openai_pipeline_model(telemetry, chat):
    # An OpenAI connection that is a Pipelines server: the user block is
    # forwarded to the provider but never reaches the span.
    chat.request.app.state.OPENAI_MODELS[MODEL_ID]["pipeline"] = True
    chat.provider.json = {
        "model": MODEL_ID,
        "choices": [
            {
                "message": {"role": "assistant", "content": COMPLETION_SENTINEL},
                "finish_reason": "stop",
            }
        ],
        "usage": USAGE,
    }

    await chat.complete(chat.form(PROMPT_SENTINEL))

    llm = assert_llm_span_tree(telemetry)
    assert llm.attributes["canchat.llm.pipeline"] is True
    assert llm.attributes["gen_ai.provider.name"] == "openai"
    assert llm.attributes["gen_ai.request.model"] == MODEL_ID
    assert llm.attributes["gen_ai.usage.output_tokens"] == 2
    assert_no_content(
        llm, PROMPT_SENTINEL, COMPLETION_SENTINEL, chat.user.email, chat.user.id
    )


PIPE_ID = "obs25_pipe"


def install_pipe(monkeypatch, chat, pipe):
    chat.request.app.state.MODELS[PIPE_ID] = {
        "id": PIPE_ID,
        "name": "Pipe",
        "owned_by": "openai",
        "pipe": {"type": "pipe"},
        "info": {"meta": {}},
    }

    async def no_tools(*args, **kwargs):
        return {}

    module = type("PipeModule", (), {"pipe": staticmethod(pipe)})()
    monkeypatch.setattr(functions_module, "get_tools_async", no_tools)
    monkeypatch.setattr(functions_module.Models, "get_model_by_id", lambda _id: None)
    monkeypatch.setattr(
        functions_module, "get_function_module_by_id", lambda _r, _id: module
    )
    monkeypatch.setattr(functions_module, "get_event_emitter", lambda _m: None)
    monkeypatch.setattr(functions_module, "get_event_call", lambda _m: None)


@pytest.mark.anyio
async def test_function_pipe_calling_a_provider_router(telemetry, chat, monkeypatch):
    # A pipe function is not itself an LLM operation; the provider call it
    # makes through the router is, and carries the gen_ai metadata.
    async def pipe(body, __request__):
        return await openai_router.generate_chat_completion(
            __request__, {**body, "model": MODEL_ID}, chat.user, bypass_filter=True
        )

    install_pipe(monkeypatch, chat, pipe)
    chat.provider.json = {
        "model": MODEL_ID,
        "choices": [{"message": {"content": COMPLETION_SENTINEL}}],
        "usage": USAGE,
    }
    form = chat.form(PROMPT_SENTINEL)
    form["model"] = PIPE_ID

    result = await chat.complete(form)

    assert result["choices"][0]["message"]["content"] == COMPLETION_SENTINEL
    llm = only_span(telemetry, "llm.chat")
    assert llm.parent.span_id == only_span(telemetry, "chat.operation").context.span_id
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL_ID,
        "gen_ai.response.model": MODEL_ID,
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL)
    assert_recorded_once(telemetry, "success", stream=False)


@pytest.mark.anyio
async def test_function_pipe_without_provider_router_has_no_llm_span(
    telemetry, chat, monkeypatch
):
    def pipe(body):
        return COMPLETION_SENTINEL

    install_pipe(monkeypatch, chat, pipe)
    form = chat.form(PROMPT_SENTINEL)
    form["model"] = PIPE_ID

    await chat.complete(form)

    assert spans_named(telemetry, "llm.chat") == []
    assert chat.provider.calls == 0
    assert metric_points(telemetry, llm_obs.LLM_REQUESTS) == []


def ollama_admin(monkeypatch):
    monkeypatch.setattr(ollama_router.Models, "get_model_by_id", lambda _id: None)

    async def ollama_url(request, model, url_idx):
        return "http://ollama.test", 0

    monkeypatch.setattr(ollama_router, "get_ollama_url", ollama_url)
    return type("U", (), {"role": "admin", "id": "u"})()


def ndjson(data):
    return (json.dumps(data) + "\n").encode()


@pytest.mark.anyio
async def test_ollama_api_chat_gen_ai_attributes(telemetry, monkeypatch):
    provider = Provider(monkeypatch, Clock())
    monkeypatch.setattr(llm_obs, "_monotonic", provider.clock)
    provider.body = [
        ndjson({"model": OLLAMA_MODEL, "message": {"content": COMPLETION_SENTINEL}}),
        ndjson(
            {
                "model": OLLAMA_MODEL,
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 11,
                "eval_count": 3,
            }
        ),
    ]

    response = await ollama_router.generate_chat_completion(
        ollama_request(telemetry),
        {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
            "stream": True,
        },
        url_idx=0,
        user=ollama_admin(monkeypatch),
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": OLLAMA_MODEL,
        "gen_ai.response.model": OLLAMA_MODEL,
        "gen_ai.response.finish_reasons": ("stop",),
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 3,
    }
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL)


@pytest.mark.anyio
async def test_ollama_api_generate_gen_ai_attributes(telemetry, monkeypatch):
    provider = Provider(monkeypatch, Clock())
    monkeypatch.setattr(llm_obs, "_monotonic", provider.clock)
    # ``/api/generate`` always proxies the provider body as a stream; a
    # ``stream: false`` answer is a single NDJSON line.
    provider.body = [
        ndjson(
            {
                "model": OLLAMA_MODEL,
                "response": COMPLETION_SENTINEL,
                "done": True,
                "done_reason": "length",
                "prompt_eval_count": 4,
                "eval_count": 1,
            }
        )
    ]

    response = await ollama_router.generate_completion(
        ollama_request(telemetry),
        ollama_router.GenerateCompletionForm(
            model=MODEL_ID, prompt=PROMPT_SENTINEL, stream=False
        ),
        url_idx=0,
        user=None,
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "text_completion",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": MODEL_ID,
        "gen_ai.response.model": OLLAMA_MODEL,
        "gen_ai.response.finish_reasons": ("length",),
        "gen_ai.usage.input_tokens": 4,
        "gen_ai.usage.output_tokens": 1,
    }
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL)


@pytest.mark.anyio
async def test_ollama_openai_chat_gen_ai_attributes(telemetry, monkeypatch):
    provider = Provider(monkeypatch, Clock())
    monkeypatch.setattr(llm_obs, "_monotonic", provider.clock)

    response = await ollama_router.generate_openai_chat_completion(
        ollama_request(telemetry),
        {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
            "stream": True,
        },
        url_idx=0,
        user=ollama_admin(monkeypatch),
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": OLLAMA_MODEL,
        "gen_ai.response.model": MODEL_ID,
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    assert_no_content(llm, PROMPT_SENTINEL)


@pytest.mark.anyio
async def test_ollama_openai_completions_endpoint(telemetry, monkeypatch):
    # ``/v1/completions`` had no provider operation before OBS-25.
    provider = Provider(monkeypatch, Clock())
    monkeypatch.setattr(llm_obs, "_monotonic", provider.clock)

    def text(value, finish_reason=None):
        return sse(
            {
                "model": OLLAMA_MODEL,
                "choices": [
                    {"index": 0, "text": value, "finish_reason": finish_reason}
                ],
            }
        )

    provider.body = [
        2.0,
        text(""),
        0.5,
        text(COMPLETION_SENTINEL),
        text("", "stop"),
        sse({"model": OLLAMA_MODEL, "choices": [], "usage": USAGE}),
        b"data: [DONE]\n",
    ]

    response = await ollama_router.generate_openai_completion(
        ollama_request(telemetry),
        {
            "model": MODEL_ID,
            "prompt": PROMPT_SENTINEL,
            "stream": True,
            "metadata": {"task": "autocomplete_generation"},
        },
        url_idx=0,
        user=ollama_admin(monkeypatch),
    )
    [_ async for _ in response.body_iterator]

    llm = only_span(telemetry, "llm.chat")
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "text_completion",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": OLLAMA_MODEL,
        "gen_ai.response.model": OLLAMA_MODEL,
        "gen_ai.response.finish_reasons": ("stop",),
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 2,
    }
    assert_no_content(llm, PROMPT_SENTINEL, COMPLETION_SENTINEL)
    assert_recorded_once(
        telemetry,
        "success",
        stream=True,
        ttft=HEADER_DELAY + 2.5,
        task="autocomplete_generation",
        provider="ollama",
        model=OLLAMA_MODEL,
    )


def test_gen_ai_values_are_bounded(telemetry):
    # Unconfigured model: no request model. Provider-reported values that are
    # not identifiers or known finish reasons are dropped or bucketed.
    operation = llm_obs.LLMOperation.start(
        fake_request(telemetry.metrics),
        provider="openai",
        model=None,
        stream=False,
        operation_name="not-an-operation",
        request_model="anything",
    )
    operation.observe_response(
        {
            "model": f"{PROMPT_SENTINEL} with spaces",
            "choices": [
                {"finish_reason": "stop"},
                {"finish_reason": PROMPT_SENTINEL},
                {"finish_reason": "stop"},
                {"finish_reason": None},
            ],
        }
    )
    operation.finish()

    llm = only_span(telemetry, "llm.chat")
    assert gen_ai(llm) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.response.finish_reasons": ("stop", "other"),
    }
    assert_no_content(llm, PROMPT_SENTINEL)


def test_start_failure_sets_no_gen_ai(telemetry, monkeypatch):
    def broken_tracer():
        raise RuntimeError("tracer unavailable")

    monkeypatch.setattr(llm_obs, "_tracer", broken_tracer)
    operation = llm_obs.LLMOperation.start(
        fake_request(telemetry.metrics),
        provider="openai",
        model=MODEL_ID,
        stream=False,
        request_model=MODEL_ID,
    )
    operation.observe_response({"model": MODEL_ID, "usage": USAGE})
    operation.finish()

    assert spans_named(telemetry, "llm.chat") == []


@pytest.mark.anyio
async def test_malformed_response_metadata_never_fails_the_response(telemetry, chat):
    chat.provider.json = {
        "model": {"name": UPSTREAM_MODEL},
        "choices": [
            {"message": {"content": "answer"}, "finish_reason": ["stop"]},
            {"message": {"content": "answer"}, "finish_reason": {"type": "stop"}},
            "not-a-choice",
        ],
        "done_reason": 42,
        "usage": USAGE,
    }

    result = await chat.complete(chat.form())

    assert result["choices"][0]["message"]["content"] == "answer"
    llm = only_span(telemetry, "llm.chat")
    assert llm.attributes["canchat.outcome"] == "success"
    assert "gen_ai.response.model" not in llm.attributes
    assert "gen_ai.response.finish_reasons" not in llm.attributes
    assert llm.attributes["gen_ai.usage.input_tokens"] == 7
    assert_recorded_once(telemetry, "success", stream=False)
