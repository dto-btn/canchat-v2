"""
OBS-10 / OBS-17: the crew-mcp-query socket handler must not log prompt text,
Graph token fragments or user details at any level, on the success, error and
exception paths. Uses the OBS-17 canary harness, which checks stdout, stderr,
every log record, the production JSON and OTLP handlers, and spans.
"""

import asyncio
import logging
import sys
import types

import pytest

# Prevent uvicorn import errors in test environment
sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))

from open_webui.socket import main as socket_main  # noqa: E402
from open_webui.test.util.canary_harness import CanaryLeakError  # noqa: E402

SID = "sid-canary"


class FakeSio:
    def __init__(self):
        self.emitted = []

    async def emit(self, event, data=None, room=None, **kwargs):
        self.emitted.append((event, data, room))


class FakeCrewManager:
    def __init__(self, raise_error=False, tools=True, usage=None):
        self.raise_error = raise_error
        self.tools = tools
        self.usage = usage or {}
        self.tokens_seen = []
        self.azure_config = types.SimpleNamespace(deployment=None)

    def get_available_tools(self):
        return [{"name": "fake_tool"}] if self.tools else []

    def run_intelligent_crew(self, query, selected_tools, user_access_token):
        self.tokens_seen.append(user_access_token)
        if self.raise_error:
            raise RuntimeError(
                f"provider rejected prompt: {query} token={user_access_token}"
            )
        return (f"answer to {query}", self.usage, None)


def _run_handler(monkeypatch, harness, manager, sid=SID):
    canaries = harness.canaries
    fake_sio = FakeSio()
    monkeypatch.setattr(socket_main, "sio", fake_sio)
    monkeypatch.setattr(
        socket_main,
        "SESSION_POOL",
        {
            SID: {
                "id": "user-1",
                "email": canaries.email,
                "name": canaries.name,
                "graph_access_token": canaries.token,
            }
        },
    )
    fake_module = types.ModuleType("mcp_backend.routers.crew_mcp")
    fake_module.crew_mcp_manager = manager
    monkeypatch.setitem(sys.modules, "mcp_backend.routers.crew_mcp", fake_module)

    asyncio.run(
        socket_main.crew_mcp_query(
            sid,
            {
                "query": canaries.prompt,
                "model": "gpt-test",
                "selected_tools": ["fake_tool"],
                "chat_id": "chat-1",
            },
        )
    )
    return fake_sio


def _events(fake_sio, name):
    return [data for event, data, _ in fake_sio.emitted if event == name]


@pytest.mark.parametrize("delegated", ["true", "false"])
def test_success_path_does_not_log_prompt_token_or_user(
    monkeypatch, canary_harness, delegated
):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", delegated)
    manager = FakeCrewManager()

    fake_sio = _run_handler(monkeypatch, canary_harness, manager)

    assert _events(fake_sio, "crew-mcp-result")
    assert manager.tokens_seen == [canary_harness.canaries.token]
    output = "\n".join(r.getMessage() for r in canary_harness.records)
    assert f"query_len={len(canary_harness.canaries.prompt)}" in output
    assert "graph_access_token present=True" in output
    canary_harness.assert_no_canaries()


def test_exception_path_does_not_log_prompt_or_token(monkeypatch, canary_harness):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    manager = FakeCrewManager(raise_error=True)

    fake_sio = _run_handler(monkeypatch, canary_harness, manager)

    assert _events(fake_sio, "crew-mcp-error")
    errors = [r for r in canary_harness.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["CrewMCP WebSocket error: RuntimeError"]
    canary_harness.assert_no_canaries()


def test_unknown_session_error_path(monkeypatch, canary_harness):
    fake_sio = _run_handler(
        monkeypatch, canary_harness, FakeCrewManager(), sid="sid-unknown"
    )

    assert _events(fake_sio, "crew-mcp-error")[0]["code"] == 401
    assert any(r.levelno >= logging.ERROR for r in canary_harness.records)
    canary_harness.assert_no_canaries()


def test_no_tools_error_path(monkeypatch, canary_harness):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    manager = FakeCrewManager(tools=False)

    fake_sio = _run_handler(monkeypatch, canary_harness, manager)

    assert _events(fake_sio, "crew-mcp-error")[0]["code"] == 503
    assert manager.tokens_seen == []
    canary_harness.assert_no_canaries()


@pytest.mark.xfail(
    strict=True,
    raises=CanaryLeakError,
    reason="OBS-18: the token-metrics warning formats the exception text",
)
def test_metrics_failure_does_not_log_exception_text(monkeypatch, canary_harness):
    from open_webui.models import message_metrics

    def fail(*args, **kwargs):
        raise RuntimeError(f"insert failed for {canary_harness.canaries.email}")

    monkeypatch.setattr(message_metrics.MessageMetrics, "insert_new_metrics", fail)
    manager = FakeCrewManager(usage={"total_tokens": 3})

    fake_sio = _run_handler(monkeypatch, canary_harness, manager)

    assert _events(fake_sio, "crew-mcp-result")
    canary_harness.assert_no_canaries()
