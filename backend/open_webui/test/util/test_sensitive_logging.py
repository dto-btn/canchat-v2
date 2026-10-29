"""
OBS-10 review follow-ups: tool output, bound tool context and OAuth callback
error values must not reach logs, and logging must not break tool isolation.

Every sink is checked with the OBS-17 canary harness (``test/util/canary_harness.py``).
"""

import asyncio
import json
import sys
import types

import pytest

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

# Import before any capture is installed: env.py reconfigures root logging with force=True.
from authlib.integrations.base_client.errors import OAuthError  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from open_webui.utils import middleware  # noqa: E402
from open_webui.utils import oauth as oauth_module  # noqa: E402
from open_webui.utils.tools import apply_extra_params_to_tool_function  # noqa: E402

# Parametrize placeholders, replaced by per-test canaries at runtime.
PROMPT = object()
OAUTH_ERROR = object()


def _messages(harness):
    return "\n".join(record.getMessage() for record in harness.records)


def _with_prompt(value, prompt):
    if value is PROMPT:
        return prompt
    if isinstance(value, dict):
        return {
            _with_prompt(k, prompt): _with_prompt(v, prompt) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_with_prompt(v, prompt) for v in value]
    if isinstance(value, tuple) and value and value[0] == "json":
        return json.dumps(_with_prompt(value[1], prompt))
    return value


def _run_tools_handler(monkeypatch, harness, tool_calls):
    canaries = harness.canaries
    tool_result = canaries.add("tool result", f"{canaries.tag}-tool-result-do-not-log")
    tool_calls = _with_prompt(tool_calls, canaries.prompt)
    calls = []

    async def echo(text: str, __messages__=None, __user__=None):
        calls.append(text)
        return tool_result

    extra_params = {
        "__messages__": [{"role": "user", "content": canaries.prompt}],
        "__user__": {"id": "user-1", "email": canaries.email},
    }
    tool_entry = {
        "toolkit_id": "kit",
        "callable": apply_extra_params_to_tool_function(echo, extra_params),
        "spec": {
            "name": "echo",
            "parameters": {"properties": {"text": {"type": "string"}}},
        },
        "file_handler": False,
        "citation": True,
    }
    tools = {"echo": tool_entry, "broken": {**tool_entry}}

    async def fake_get_tools_async(*args, **kwargs):
        return tools

    async def fake_generate_chat_completion(request, form_data, user):
        return {"choices": [{"message": {"content": json.dumps(tool_calls)}}]}

    monkeypatch.setattr(middleware, "get_task_model_id", lambda *a, **k: "task")
    monkeypatch.setattr(middleware, "get_tools_async", fake_get_tools_async)
    monkeypatch.setattr(
        middleware, "generate_chat_completion", fake_generate_chat_completion
    )

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="",
                )
            )
        )
    )
    body = {
        "model": "chat-model",
        "messages": [{"role": "user", "content": canaries.prompt}],
        "metadata": {"tool_ids": ["kit"]},
    }

    _, flags = asyncio.run(
        middleware.chat_completion_tools_handler(
            request, body, types.SimpleNamespace(id="user-1"), {"task": {}}, {}
        )
    )
    return calls, flags, tool_result


def test_tool_output_and_bound_context_not_logged(monkeypatch, canary_harness):
    calls, flags, tool_result = _run_tools_handler(
        monkeypatch, canary_harness, [{"name": "echo", "parameters": {"text": "hello"}}]
    )

    assert calls == ["hello"]
    assert flags["sources"][0]["document"] == [tool_result]
    assert "tool_contexts: 1 sources" in _messages(canary_harness)
    canary_harness.assert_no_canaries()


def test_invalid_tool_parameters_do_not_abort_later_tool_calls(
    monkeypatch, canary_harness
):
    calls, flags, _ = _run_tools_handler(
        monkeypatch,
        canary_harness,
        [
            {"name": "broken", "parameters": None},
            {"name": "echo", "parameters": {"text": "after-broken"}},
        ],
    )

    assert calls == ["after-broken"]
    assert len(flags["sources"]) == 2
    assert "params=NoneType" in _messages(canary_harness)
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize(
    "invalid_call",
    [
        {"name": PROMPT},
        {"name": [PROMPT]},
        ("json", PROMPT),
    ],
    ids=["name", "name-list", "json-string"],
)
def test_invalid_tool_calls_not_logged_or_abort_later_calls(
    monkeypatch, canary_harness, invalid_call
):
    calls, flags, _ = _run_tools_handler(
        monkeypatch,
        canary_harness,
        [invalid_call, {"name": "echo", "parameters": {"text": "hello"}}],
    )

    assert calls == ["hello"]
    assert len(flags["sources"]) == 1
    assert "Tool calls extracted: 2" in _messages(canary_harness)
    canary_harness.assert_no_canaries()


def test_undeclared_tool_parameter_keys_not_logged(monkeypatch, canary_harness):
    calls, flags, tool_result = _run_tools_handler(
        monkeypatch,
        canary_harness,
        [{"name": "echo", "parameters": {PROMPT: "secret", "text": "hello"}}],
    )

    assert calls == ["hello"]
    assert flags["sources"][0]["document"] == [tool_result]
    assert "Tool call: echo params=['text']" in _messages(canary_harness)
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize(
    "error_code, expected",
    [(OAUTH_ERROR, "other"), ("access_denied", "access_denied")],
    ids=["unknown-code", "allowlisted-code"],
)
def test_oauth_callback_error_code_is_allowlisted(
    monkeypatch, canary_harness, error_code, expected
):
    canaries = canary_harness.canaries
    oauth_error = canaries.add("oauth error", f"{canaries.tag}-oauth-error-do-not-log")
    if error_code is OAUTH_ERROR:
        error_code = oauth_error

    class FakeClient:
        async def authorize_access_token(self, request):
            raise OAuthError(error=error_code, description=oauth_error)

    monkeypatch.setattr(oauth_module, "OAUTH_PROVIDERS", {"fake": {}})
    manager = oauth_module.OAuthManager.__new__(oauth_module.OAuthManager)
    manager.get_client = lambda provider: FakeClient()

    with pytest.raises(HTTPException):
        asyncio.run(manager.handle_callback("fake", None, None))

    assert f"OAuth callback error: OAuthError {expected}" in _messages(canary_harness)
    canary_harness.assert_no_canaries()
