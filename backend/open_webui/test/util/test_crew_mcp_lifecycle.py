import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def crew_mcp_module(monkeypatch):
    from mcp_backend.integration import crew_mcp_integration

    monkeypatch.setattr(
        crew_mcp_integration.AzureConfig, "validate_config", lambda self: True
    )
    return crew_mcp_integration


@pytest.mark.parametrize("endpoint", ["https://example.test", "https://example.test/"])
def test_foundry_v1_uses_openai_chat_completions(
    crew_mcp_module, monkeypatch, endpoint
):
    requests = []

    def send(_client, request, **_kwargs):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    manager = crew_mcp_module.CrewMCPManager()
    manager.azure_config = crew_mcp_module.AzureConfig(
        endpoint=endpoint,
        api_key="test-key",
        deployment="gpt-4o-mini",
        api_mode="v1",
    )

    assert manager.get_azure_llm_config().call("hello") == "ok"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://example.test/openai/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "gpt-4o-mini"


def test_legacy_azure_configuration_is_preserved(crew_mcp_module, monkeypatch):
    monkeypatch.setattr(crew_mcp_module, "LLM", lambda **kwargs: kwargs)
    for key in ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"):
        monkeypatch.setenv(key, "initial")
    manager = crew_mcp_module.CrewMCPManager()
    manager.azure_config = crew_mcp_module.AzureConfig(
        endpoint="https://example.test",
        api_key="test-key",
        deployment="test-deployment",
        api_version="test-version",
        api_mode="legacy",
    )

    assert manager.get_azure_llm_config() == {
        "model": "azure/test-deployment",
        "timeout": 30,
    }
    assert crew_mcp_module.os.environ["AZURE_API_BASE"] == "https://example.test"
    assert crew_mcp_module.os.environ["AZURE_API_VERSION"] == "test-version"


def test_invalid_azure_api_mode_fails_explicitly(crew_mcp_module):
    manager = crew_mcp_module.CrewMCPManager()
    manager.azure_config.api_mode = "invalid"

    with pytest.raises(ValueError, match="CREWAI_AZURE_OPENAI_API_MODE"):
        manager.get_azure_llm_config()


def test_adapters_start_lazily_and_close_their_context_managers(
    crew_mcp_module, monkeypatch
):
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    created_sessions = []

    class FakeSession:
        def __init__(self):
            self.thread = SimpleNamespace(is_alive=lambda: True)
            self.close_count = 0

        def close(self):
            self.close_count += 1

    def open_session(_name, _token):
        session = FakeSession()
        created_sessions.append(session)
        tool = SimpleNamespace(
            definition=SimpleNamespace(name="tool", description="description")
        )
        return session, [tool]

    owner = BuiltinSessions()
    monkeypatch.setattr(owner, "_open", open_session)
    monkeypatch.setattr(crew_mcp_module, "builtin_sessions", owner)
    manager = crew_mcp_module.CrewMCPManager()
    manager._sharepoint_server_paths = {}

    assert created_sessions == []

    assert len(manager.get_available_tools()) == 2
    assert len(manager.get_available_tools()) == 2
    assert len(created_sessions) == 2

    manager.cleanup_mcp_adapters()
    assert [session.close_count for session in created_sessions] == [1, 1]


def test_sharepoint_adapter_closes_when_crew_execution_fails(
    crew_mcp_module, monkeypatch, tmp_path
):
    closed = []

    @contextmanager
    def session(_name, _token):
        try:
            yield [SimpleNamespace(name="sharepoint_tool")]
        finally:
            closed.append(True)

    class FailingCrew:
        def __init__(self, **_kwargs):
            pass

        def kickoff(self):
            raise RuntimeError("simulated crew failure")

    monkeypatch.setattr(
        crew_mcp_module, "builtin_sessions", SimpleNamespace(session=session)
    )
    monkeypatch.setattr(crew_mcp_module, "Agent", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Task", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Crew", FailingCrew)

    server_path = tmp_path / "sharepoint_server.py"
    server_path.touch()
    manager = crew_mcp_module.CrewMCPManager()
    manager._sharepoint_server_paths = {"MPO": server_path}
    monkeypatch.setattr(manager, "get_azure_llm_config", lambda: object())
    monkeypatch.setattr(manager, "_crewai_tools", lambda tools: tools)

    result, _usage = manager.run_sharepoint_crew("MPO", "test query")

    assert "simulated crew failure" in result
    assert closed == [True]


def test_sharepoint_sessions_keep_concurrent_tokens_separate(
    crew_mcp_module, monkeypatch, tmp_path
):
    seen_tokens = []
    barrier = Barrier(2)

    @contextmanager
    def session(_name, token):
        seen_tokens.append(token)
        barrier.wait(timeout=5)
        yield [SimpleNamespace(name="tool")]

    class FailingCrew:
        def __init__(self, **_kwargs):
            pass

        def kickoff(self):
            raise RuntimeError("simulated failure")

    monkeypatch.setenv("USER_JWT_TOKEN", "unrelated-global-token")
    monkeypatch.setattr(
        crew_mcp_module, "builtin_sessions", SimpleNamespace(session=session)
    )
    monkeypatch.setattr(crew_mcp_module, "Agent", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Task", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Crew", FailingCrew)

    server_path = tmp_path / "sharepoint_server.py"
    server_path.touch()
    manager = crew_mcp_module.CrewMCPManager()
    manager._sharepoint_server_paths = {"MPO": server_path}
    monkeypatch.setattr(manager, "get_azure_llm_config", lambda: object())
    monkeypatch.setattr(manager, "_crewai_tools", lambda tools: tools)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(manager.run_sharepoint_crew, "MPO", "query", token)
            for token in ("user-one", "user-two")
        ]
        for future in futures:
            future.result(timeout=10)

    assert sorted(seen_tokens) == ["user-one", "user-two"]
    assert crew_mcp_module.os.environ["USER_JWT_TOKEN"] == "unrelated-global-token"


def test_sharepoint_routing_passes_request_token(crew_mcp_module, monkeypatch):
    manager = crew_mcp_module.CrewMCPManager()
    captured = []

    def run_sharepoint(department, query, token):
        captured.append((department, query, token))
        return "ok", {}

    monkeypatch.setattr(manager, "run_sharepoint_crew", run_sharepoint)
    assert manager.run_intelligent_crew(
        "query", ["mcp_mpo_sharepoint_server_search"], "request-token"
    ) == ("ok", {}, "mpo_sharepoint_server")
    assert captured == [("MPO", "query", "request-token")]


@pytest.mark.parametrize(
    "route_name", ["run_crew_query", "run_multi_server_crew_query"]
)
@pytest.mark.parametrize("delegated", [True, False])
def test_crew_routes_pass_only_the_current_request_token(
    monkeypatch, route_name, delegated
):
    from mcp_backend.routers import crew_mcp

    captured = []

    async def get_token(_request):
        return "request-token"

    def run_crew(query, selected_tools, token):
        captured.append((query, selected_tools, token))
        return "ok", {}, "mpo_sharepoint_server"

    manager = SimpleNamespace(
        get_available_tools=lambda: [{"name": "tool"}], run_intelligent_crew=run_crew
    )
    monkeypatch.setattr(crew_mcp, "crew_mcp_manager", manager)
    monkeypatch.setattr(crew_mcp, "get_graph_access_token_for_user", get_token)
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", str(delegated).lower())
    request = crew_mcp.CrewMCPQuery(query="query", selected_tools=["tool"])

    response = asyncio.run(
        getattr(crew_mcp, route_name)(
            object(), request, user=SimpleNamespace(id="user"), auth_token=None
        )
    )

    assert response.success
    assert captured == [("query", ["tool"], "request-token" if delegated else None)]


def test_sharepoint_session_parameters_use_only_request_token(monkeypatch):
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    monkeypatch.setenv("SHAREPOINT_DEPARTMENTS", "MPO")
    monkeypatch.setenv("USER_JWT_TOKEN", "unrelated-global-token")
    owner = BuiltinSessions()

    assert (
        owner._parameters("mpo_sharepoint_server", "user-one").env["USER_JWT_TOKEN"]
        == "user-one"
    )
    assert "USER_JWT_TOKEN" not in owner._parameters("mpo_sharepoint_server", None).env
    assert "USER_JWT_TOKEN" not in owner._parameters("time_server", None).env


def test_shutdown_waits_for_active_sessions(monkeypatch):
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    owner = BuiltinSessions()
    closed = Event()
    fake_session = SimpleNamespace(
        thread=SimpleNamespace(is_alive=lambda: True), close=closed.set
    )
    monkeypatch.setattr(owner, "_open", lambda _name, _token: (fake_session, []))

    with ThreadPoolExecutor(max_workers=1) as executor:
        with owner.session("time_server"):
            cleanup = executor.submit(owner.cleanup)
            deadline = time.monotonic() + 5
            while not owner._closing and time.monotonic() < deadline:
                time.sleep(0.01)
            assert owner._closing
            assert not closed.is_set()
            with pytest.raises(RuntimeError, match="shutting down"):
                with owner.session("time_server"):
                    pass
        cleanup.result(timeout=5)
    assert closed.is_set()


def test_failed_builtin_start_is_not_reported_as_idle(monkeypatch):
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    owner = BuiltinSessions()

    def fail(_name, _token):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(owner, "_parameters", fail)
    with pytest.raises(RuntimeError, match="startup failed"):
        owner.describe("time_server")
    assert owner.status("time_server") == "stopped"


def test_failed_start_retains_live_session_until_cleanup(monkeypatch):
    from mcp_backend.management import builtin_sessions as sessions_module

    owner = sessions_module.BuiltinSessions()
    closed = []

    class FailedSession:
        def __init__(self, _parameters):
            self.thread = SimpleNamespace(is_alive=lambda: True)

        def start(self):
            raise TimeoutError("startup cleanup timed out")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(sessions_module, "_Session", FailedSession)
    with pytest.raises(TimeoutError, match="startup cleanup timed out"):
        owner.describe("time_server")
    with pytest.raises(sessions_module.BuiltinServerBusy, match="requires cleanup"):
        owner.describe("time_server")
    owner.cleanup()
    assert closed == [True]


def test_cleanup_attempts_remaining_sessions_after_failure(monkeypatch):
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    owner = BuiltinSessions()
    closed = []

    def fail():
        raise TimeoutError("failed to close")

    owner._sessions = {
        "time_server": (SimpleNamespace(close=fail), []),
        "news_server": (SimpleNamespace(close=lambda: closed.append("news")), []),
    }

    with pytest.raises(ExceptionGroup, match="Failed to clean up"):
        owner.cleanup()
    assert closed == ["news"]
    assert list(owner._sessions) == ["time_server"]
    monkeypatch.setattr(owner._sessions["time_server"][0], "close", lambda: None)
    owner.cleanup()
    assert not owner._sessions


def test_default_server_registration_does_not_start_sessions(monkeypatch):
    from mcp_backend.management import mcp_manager as manager_module
    from mcp_backend.management.builtin_sessions import BuiltinSessions

    owner = BuiltinSessions()
    monkeypatch.setattr(manager_module, "builtin_sessions", owner)
    monkeypatch.setenv("SHAREPOINT_DEPARTMENTS", "")
    manager = manager_module.FastMCPManager()

    asyncio.run(manager.initialize_default_servers())

    assert manager.get_server_status("time_server") == "idle"
    assert manager.get_server_status("news_server") == "idle"
    assert not owner._sessions
    assert not asyncio.run(manager.restart_server("unknown_sharepoint_server"))
    manager.add_server_config(
        "external_sharepoint_server", ["python", "external.py"], transport="http"
    )
    assert not manager.is_builtin_server("external_sharepoint_server")
    with pytest.raises(ValueError, match="reserved"):
        manager.add_server_config("time_server", ["python", "external.py"])


@pytest.mark.skipif(sys.platform != "linux", reason="Uses Linux process checks")
def test_real_builtin_session_reuses_process_and_cleans_up(
    crew_mcp_module, monkeypatch, tmp_path
):
    from fastapi import HTTPException
    from mcp import StdioServerParameters
    from mcp_backend.management.builtin_sessions import (
        BuiltinServerBusy,
        BuiltinSessions,
    )
    from mcp_backend.management import builtin_sessions as builtin_module
    from mcp_backend.management import mcp_manager as manager_module
    from mcp_backend.routers.mcp import (
        MCPToolCallForm,
        call_mcp_tool,
        get_all_mcp_tools,
        get_builtin_servers,
        restart_builtin_server,
    )

    pid_file = tmp_path / "server.pid"
    wrapper = tmp_path / "time_server.py"
    server_script = (
        Path(builtin_module.__file__).parent.parent
        / "servers"
        / "fastmcp_time_server.py"
    )
    wrapper.write_text(
        "import os, pathlib, runpy\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        f"runpy.run_path({str(server_script)!r}, run_name='__main__')\n"
    )
    owner = BuiltinSessions()
    monkeypatch.setattr(
        owner,
        "_parameters",
        lambda _name, _token: StdioServerParameters(
            command=sys.executable, args=[str(wrapper)]
        ),
    )
    monkeypatch.setattr(manager_module, "builtin_sessions", owner)
    monkeypatch.setattr(crew_mcp_module, "builtin_sessions", owner)
    manager = manager_module.FastMCPManager()
    manager.add_server_config(
        "time_server", [sys.executable, str(wrapper)], is_builtin=True
    )
    crew_manager = crew_mcp_module.CrewMCPManager()
    crew_manager.news_server_path = tmp_path / "missing-news"
    crew_manager._sharepoint_server_paths = {}

    def wait_for_exit(pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        pytest.fail(f"Server process {pid} did not exit")

    assert owner.status("time_server") == "idle"
    assert not pid_file.exists()
    try:
        admin_tools = asyncio.run(manager.get_tools_from_server("time_server"))
        first_pid = int(pid_file.read_text())
        assert admin_tools[0]["name"] == "get_current_time"
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(mcp_manager=manager, config={}))
        )
        status = asyncio.run(get_builtin_servers(request, user=object()))
        assert status["servers"][0]["status"] == "running"
        from mcp_backend.routers import crew_mcp

        monkeypatch.setattr(crew_mcp, "crew_mcp_manager", None)
        discovered = asyncio.run(get_all_mcp_tools(request))["tools"]
        assert discovered[0]["name"] == "get_current_time"
        assert discovered[0]["mcp_server_idx"] == 0
        assert crew_manager.get_available_tools()[0]["name"] == "get_current_time"
        assert int(pid_file.read_text()) == first_pid
        with owner.session("time_server") as tools:
            assert tools[0].definition.name == "get_current_time"
            assert not tools[0].call({"timezone": "UTC"}).isError
            crew_tool = crew_manager._crewai_tools(tools)[0]
            assert crew_tool.run(timezone="UTC")
            result = asyncio.run(
                manager.call_tool(
                    "time_server", "get_current_time", {"timezone": "UTC"}
                )
            )
            assert not result.isError
            response = asyncio.run(
                call_mcp_tool(
                    request,
                    MCPToolCallForm(
                        name="get_current_time", arguments={"timezone": "UTC"}
                    ),
                    user=object(),
                )
            )
            assert response["content"][0]["text"]
            with pytest.raises(BuiltinServerBusy):
                owner.restart("time_server")
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    restart_builtin_server(request, "time_server", user=object())
                )
            assert exc.value.status_code == 409
        with owner.session("time_server"):
            assert int(pid_file.read_text()) == first_pid
        owner.restart("time_server")
        wait_for_exit(first_pid)
        second_pid = int(pid_file.read_text())
        assert second_pid != first_pid
    finally:
        asyncio.run(manager.cleanup())
    wait_for_exit(second_pid)
    assert owner.status("time_server") == "idle"
