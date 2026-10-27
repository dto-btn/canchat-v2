"""OBS-17: source-confidentiality canaries for the HTTP chat path.

Requests to ``POST /api/chat/completions`` carry canary prompts, user identity,
bearer credentials and request ids. The success, error, exception and DEBUG
paths must keep them out of every operational sink checked by the canary harness.
"""

import importlib
import sys
import types
from types import SimpleNamespace

import pytest

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from fastapi.testclient import TestClient  # noqa: E402

from open_webui.utils import middleware  # noqa: E402
from open_webui.utils.auth import get_verified_user  # noqa: E402

# ``open_webui.main`` is shadowed by the CLI ``main`` function in the package.
main = importlib.import_module("open_webui.main")

MODEL_ID = "canary-model"


@pytest.fixture
def chat(monkeypatch, canary_harness):
    canaries = canary_harness.canaries
    user = SimpleNamespace(
        id="user-1",
        email=canaries.email,
        name=canaries.name,
        role="admin",
        settings=None,
    )
    provider_calls = []

    async def fake_provider(request, form_data, user):
        provider_calls.append(form_data)
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": canaries.response},
                    "finish_reason": "stop",
                }
            ],
        }

    async def no_filters(request, body, model, extra_params):
        return body, {}

    monkeypatch.setattr(main, "chat_completion_handler", fake_provider)
    monkeypatch.setattr(
        middleware, "chat_completion_filter_functions_handler", no_filters
    )
    monkeypatch.setattr(
        main.app.state,
        "MODELS",
        {MODEL_ID: {"id": MODEL_ID, "name": "Canary model", "info": {}}},
        raising=False,
    )
    monkeypatch.setitem(main.app.dependency_overrides, get_verified_user, lambda: user)

    client = TestClient(main.app, raise_server_exceptions=False)

    def post(model=MODEL_ID, **extra):
        return client.post(
            "/api/chat/completions",
            headers={
                "Authorization": f"Bearer {canaries.api_key}",
                "Cookie": f"token={canaries.token}",
                # Invalid request id: the middleware must replace it, not log it.
                "X-Request-Id": f"{canaries.email} {canaries.password}",
                "User-Agent": f"canary-agent {canaries.name}",
            },
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": f"secret {canaries.password}"},
                    {"role": "user", "content": canaries.prompt},
                ],
                **extra,
            },
        )

    return SimpleNamespace(post=post, provider_calls=provider_calls)


def _messages(harness):
    return [record.getMessage() for record in harness.records]


def test_success_path_keeps_canaries_out_of_sinks(chat, canary_harness):
    response = chat.post()

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == (
        canary_harness.canaries.response
    )
    assert len(chat.provider_calls) == 1
    assert any(r.name == "open_webui.access" for r in canary_harness.records)
    canary_harness.assert_no_canaries()


def test_unknown_model_error_path_keeps_canaries_out_of_sinks(chat, canary_harness):
    response = chat.post(model=f"missing-{canary_harness.canaries.tag}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Model not found"
    assert chat.provider_calls == []
    canary_harness.assert_no_canaries()


def test_provider_exception_path_keeps_canaries_out_of_sinks(
    chat, canary_harness, monkeypatch
):
    canaries = canary_harness.canaries

    async def failing_provider(request, form_data, user):
        raise RuntimeError(
            f"upstream rejected {canaries.prompt} for {canaries.email} "
            f"with {canaries.api_key}"
        )

    monkeypatch.setattr(main, "chat_completion_handler", failing_provider)

    response = chat.post()

    assert response.status_code == 400
    canary_harness.assert_no_canaries()


def test_debug_path_runs_debug_logging_without_canaries(chat, canary_harness):
    response = chat.post()

    assert response.status_code == 200
    assert any(r.levelname == "DEBUG" for r in canary_harness.records)
    canary_harness.assert_no_canaries()


def test_tool_handler_exception_keeps_canaries_out_of_sinks(
    chat, canary_harness, monkeypatch
):
    canaries = canary_harness.canaries

    async def failing_tools(request, body, user, models, extra_params):
        raise RuntimeError(f"tool lookup failed for {canaries.prompt}")

    monkeypatch.setattr(middleware, "chat_completion_tools_handler", failing_tools)

    response = chat.post(tool_ids=["kit"])

    assert response.status_code == 200
    canary_harness.assert_no_canaries()
