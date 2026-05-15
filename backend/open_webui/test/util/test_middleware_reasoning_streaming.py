import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest
from starlette.responses import StreamingResponse

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

retrieval_router = types.ModuleType("open_webui.routers.retrieval")


async def _unused_process_web_search(*args, **kwargs):
    return None


class _UnusedSearchForm:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


retrieval_router.process_web_search = _unused_process_web_search
retrieval_router.SearchForm = _UnusedSearchForm
sys.modules.setdefault("open_webui.routers.retrieval", retrieval_router)

retrieval_utils = types.ModuleType("open_webui.retrieval.utils")


async def _unused_get_sources_from_files(*args, **kwargs):
    return []


retrieval_utils.get_sources_from_files = _unused_get_sources_from_files
sys.modules.setdefault("open_webui.retrieval.utils", retrieval_utils)

from open_webui.utils import middleware


async def _run_streaming_response(monkeypatch, chunks):
    saved_contents = []
    emitted_events = []
    message_state = {"content": ""}
    created_task = None

    async def fake_event_emitter(event):
        emitted_events.append(event)

    def fake_get_event_emitter(_metadata):
        return fake_event_emitter

    def fake_upsert(_chat_id, _message_id, message):
        message_state.update(message)
        if "content" in message:
            saved_contents.append(message["content"])
        return SimpleNamespace(chat={"history": {"messages": {}}})

    def fake_get_message(_chat_id, _message_id):
        return dict(message_state)

    def fake_create_task(coroutine):
        nonlocal created_task
        created_task = asyncio.create_task(coroutine)
        return ("task-id", created_task)

    async def fake_stream():
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(middleware, "get_event_emitter", fake_get_event_emitter)
    monkeypatch.setattr(middleware, "create_task", fake_create_task)
    monkeypatch.setattr(middleware, "ENABLE_REALTIME_CHAT_SAVE", True)
    monkeypatch.setattr(
        middleware, "get_active_status_by_user_id", lambda _user_id: True
    )
    monkeypatch.setattr(
        middleware.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        fake_upsert,
    )
    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        fake_get_message,
    )
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda _chat_id: "Chat"
    )
    monkeypatch.setattr(
        middleware.Chats, "get_messages_by_chat_id", lambda _chat_id: {}
    )
    monkeypatch.setattr(
        middleware.MessageMetrics,
        "insert_new_metrics",
        lambda *args, **kwargs: None,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(WEBUI_URL="http://localhost:3000")
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    form_data = {"model": "test-model"}
    metadata = {
        "session_id": "session-1",
        "chat_id": "chat-1",
        "message_id": "message-1",
    }
    response = StreamingResponse(fake_stream(), media_type="text/event-stream")

    result = await middleware.process_chat_response(
        request,
        response,
        form_data,
        user,
        events=[],
        metadata=metadata,
        tasks=None,
    )

    assert result == {"status": True, "task_id": "task-id"}
    assert created_task is not None

    await created_task

    return saved_contents, emitted_events


@pytest.mark.anyio
async def test_interleaved_reasoning_stays_in_progress_until_stream_done(monkeypatch):
    """Keeps interleaved reasoning_content marked in-progress until the stream completes."""
    saved_contents, emitted_events = await _run_streaming_response(
        monkeypatch,
        [
            {"choices": [{"delta": {"reasoning_content": "Plan step 1"}}]},
            {"choices": [{"delta": {"content": "Answer starts"}}]},
            {"choices": [{"delta": {"reasoning_content": "Plan step 2"}}]},
        ],
    )

    assert len(saved_contents) >= 3
    assert 'done="false"' in saved_contents[0]
    assert all('done="true"' not in content for content in saved_contents[:-1])
    assert 'done="true"' in saved_contents[-1]
    assert "Plan step 2" in saved_contents[-1]
    assert emitted_events[-1]["data"]["done"] is True


@pytest.mark.anyio
async def test_reasoning_only_stream_persists_completed_block_at_end(monkeypatch):
    """Persists a completed reasoning block at stream end when no answer text is emitted."""
    saved_contents, emitted_events = await _run_streaming_response(
        monkeypatch,
        [
            {"choices": [{"delta": {"reasoning_content": "Plan step 1"}}]},
            {"choices": [{"delta": {"reasoning_content": "Plan step 2"}}]},
        ],
    )

    assert len(saved_contents) >= 2
    assert 'done="false"' in saved_contents[0]
    assert 'done="true"' in saved_contents[-1]
    assert "Plan step 1Plan step 2" in saved_contents[-1]
    assert emitted_events[-1]["data"]["done"] is True


@pytest.mark.anyio
async def test_reasoning_and_answer_text_are_preserved_in_final_save(monkeypatch):
    """Keeps the final answer text alongside the completed reasoning block in the saved message."""
    saved_contents, _ = await _run_streaming_response(
        monkeypatch,
        [
            {"choices": [{"delta": {"reasoning_content": "Plan step 1"}}]},
            {"choices": [{"delta": {"content": "Answer starts"}}]},
            {"choices": [{"delta": {"content": " and ends"}}]},
        ],
    )

    assert 'done="true"' in saved_contents[-1]
    assert saved_contents[-1].endswith("Answer starts and ends")


@pytest.mark.anyio
async def test_inline_think_stream_becomes_completed_and_keeps_answer_text(monkeypatch):
    """Converts inline <think> streaming content into a completed reasoning block with the answer text preserved."""
    saved_contents, emitted_events = await _run_streaming_response(
        monkeypatch,
        [
            {"choices": [{"delta": {"content": "<think>\nPlan step 1"}}]},
            {"choices": [{"delta": {"content": "Plan step 2"}}]},
            {"choices": [{"delta": {"content": "</think>\n"}}]},
            {"choices": [{"delta": {"content": "Answer starts"}}]},
        ],
    )

    assert 'done="false"' in saved_contents[0]
    assert 'done="true"' in saved_contents[-1]
    assert "Plan step 1" in saved_contents[-1]
    assert "Plan step 2" in saved_contents[-1]
    assert saved_contents[-1].endswith("Answer starts")
    assert emitted_events[-1]["data"]["done"] is True
