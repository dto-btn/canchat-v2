"""OBS-11: each crew subprocess receives only its invocation's Graph token."""

import asyncio
import importlib.util
import os
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture
def integration(monkeypatch):
    crewai = types.ModuleType("crewai")
    crewai.Agent = lambda **kwargs: types.SimpleNamespace(**kwargs)
    crewai.Task = lambda **kwargs: types.SimpleNamespace(**kwargs)
    crewai.Crew = None
    crewai.Process = types.SimpleNamespace(sequential="sequential")
    crewai.LLM = object
    tools = types.ModuleType("crewai_tools")
    tools.MCPServerAdapter = None
    mcp = types.ModuleType("mcp")
    mcp.StdioServerParameters = lambda **kwargs: types.SimpleNamespace(**kwargs)
    source = (
        Path(__file__).resolve().parents[5]
        / "mcp_backend/integration/crew_mcp_integration.py"
    )
    spec = importlib.util.spec_from_file_location("obs11_crew_integration", source)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "crewai", crewai)
        imports.setitem(sys.modules, "crewai_tools", tools)
        imports.setitem(sys.modules, "mcp", mcp)
        imports.setenv("CREWAI_DISABLE_TELEMETRY", "true")
        imports.setenv("CREWAI_TRACING_ENABLED", "false")
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def manager(integration, tmp_path):
    server = tmp_path / "sharepoint.py"
    server.touch()
    manager = integration.CrewMCPManager.__new__(integration.CrewMCPManager)
    manager._sharepoint_server_paths = {"MPO": server, "PMO": server}
    manager.get_azure_llm_config = lambda: None
    return manager


def _stub_tools(monkeypatch, integration, barrier=None):
    received = []

    class Adapter:
        def __init__(self, params):
            self.env = params.env

        def __enter__(self):
            if barrier:
                barrier.wait(timeout=10)
            return [types.SimpleNamespace(name="record_token", env=self.env)]

        def __exit__(self, *args):
            pass

    class Crew:
        def __init__(self, agents, **kwargs):
            self.tool = agents[0].tools[0]

        def kickoff(self):
            token = self.tool.env.get("USER_JWT_TOKEN")
            received.append(token)
            return token or "application-access"

    monkeypatch.setattr(integration, "MCPServerAdapter", Adapter)
    monkeypatch.setattr(integration, "Crew", Crew)
    return received


def test_parallel_crews_keep_tokens_isolated(monkeypatch, integration, manager):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    monkeypatch.setenv("USER_JWT_TOKEN", "unrelated-process-token")
    received = _stub_tools(monkeypatch, integration, threading.Barrier(2))
    with ThreadPoolExecutor(max_workers=2) as executor:
        for iteration in range(50):
            tokens = [f"user-{user}-{iteration}" for user in (1, 2)]
            futures = [
                executor.submit(
                    manager.run_intelligent_crew,
                    "Search documents",
                    ["mcp_mpo_sharepoint_server"],
                    token,
                )
                for token in tokens
            ]
            assert [future.result(timeout=15)[0] for future in futures] == tokens
    assert sorted(received) == sorted(
        f"user-{user}-{iteration}" for iteration in range(50) for user in (1, 2)
    )
    assert os.environ["USER_JWT_TOKEN"] == "unrelated-process-token"
    assert not hasattr(manager, "user_jwt_token")
    assert not hasattr(manager, "set_user_token")


@pytest.mark.parametrize(
    "selected_tools",
    [
        ["mcp_mpo_sharepoint_server"],
        ["mcp_mpo_sharepoint_server", "mcp_pmo_sharepoint_server"],
    ],
)
def test_single_user_crew(monkeypatch, integration, manager, selected_tools):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    received = _stub_tools(monkeypatch, integration)
    result = manager.run_intelligent_crew("Search", selected_tools, "user-token")
    assert result == (
        "user-token",
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "mpo_sharepoint_server",
    )
    assert received == ["user-token"]


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("user_access_token", [None, "", " ", "\t\r\n"])
def test_missing_token_rejected_without_environment_fallback(
    monkeypatch, integration, manager, direct, user_access_token
):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    monkeypatch.setenv("USER_JWT_TOKEN", "another-users-token")
    received = _stub_tools(monkeypatch, integration)
    with pytest.raises(
        integration.MissingGraphTokenError, match="Graph access token is required"
    ):
        if direct:
            manager.run_sharepoint_crew("MPO", "Search", user_access_token)
        else:
            manager.run_intelligent_crew(
                "Search", ["mcp_mpo_sharepoint_server"], user_access_token
            )
    assert received == []


def test_application_access_does_not_inherit_user_token(
    monkeypatch, integration, manager
):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "false")
    monkeypatch.setenv("USER_JWT_TOKEN", "another-users-token")
    received = _stub_tools(monkeypatch, integration)
    result = manager.run_intelligent_crew(
        "Search", ["mcp_mpo_sharepoint_server"], "unused-user-token"
    )
    assert result[0] == "application-access"
    assert received == [None]


def test_emergency_routing_keeps_invocation_token(monkeypatch, integration, manager):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    received = _stub_tools(monkeypatch, integration)
    run_sharepoint = manager.run_sharepoint_crew
    attempts = []

    def fail_once(department, query, user_access_token):
        attempts.append(user_access_token)
        if len(attempts) == 1:
            raise RuntimeError("Routing failure")
        return run_sharepoint(department, query, user_access_token)

    monkeypatch.setattr(manager, "run_sharepoint_crew", fail_once)
    result = manager.run_intelligent_crew(
        "Search", ["mcp_mpo_sharepoint_server"], "user-token"
    )
    assert result[0] == "user-token"
    assert attempts == ["user-token", "user-token"]
    assert received == ["user-token"]


@pytest.fixture
def socket_handler(monkeypatch, manager):
    from open_webui.socket import main as socket_main

    emitted = []

    async def emit(event, data=None, room=None):
        emitted.append((event, data, room))

    monkeypatch.setattr(socket_main, "sio", types.SimpleNamespace(emit=emit))
    module = types.ModuleType("mcp_backend.routers.crew_mcp")
    module.crew_mcp_manager = manager
    monkeypatch.setitem(sys.modules, "mcp_backend.routers.crew_mcp", module)
    manager.get_available_tools = lambda: [{"name": "record_token"}]
    return socket_main, emitted


def test_parallel_socket_requests_keep_authenticated_tokens(
    monkeypatch, integration, socket_handler
):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    monkeypatch.setenv("USER_JWT_TOKEN", "unrelated-process-token")
    received = _stub_tools(monkeypatch, integration, threading.Barrier(2))
    socket_main, emitted = socket_handler
    sessions = {
        "sid-1": {"id": "user-1", "graph_access_token": "user-1-token"},
        "sid-2": {"id": "user-2", "graph_access_token": "user-2-token"},
    }
    monkeypatch.setattr(socket_main, "SESSION_POOL", sessions)

    async def run_requests():
        for iteration in range(25):
            await asyncio.gather(
                *(
                    socket_main.crew_mcp_query(
                        sid,
                        {
                            "query": f"Search {iteration}",
                            "selected_tools": ["mcp_mpo_sharepoint_server"],
                            "graph_access_token": "untrusted-payload-token",
                        },
                    )
                    for sid in sessions
                )
            )

    asyncio.run(run_requests())
    results = [entry for entry in emitted if entry[0] == "crew-mcp-result"]
    assert len(results) == 50
    assert len(received) == 50
    assert not any(event == "crew-mcp-error" for event, _, _ in emitted)
    for _, data, sid in results:
        assert data["result"] == sessions[sid]["graph_access_token"]


def test_socket_missing_token_returns_clear_error(
    monkeypatch, integration, socket_handler
):
    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    monkeypatch.setenv("USER_JWT_TOKEN", "another-users-token")
    received = _stub_tools(monkeypatch, integration)
    socket_main, emitted = socket_handler
    monkeypatch.setattr(socket_main, "SESSION_POOL", {"sid-1": {"id": "user-1"}})
    asyncio.run(
        socket_main.crew_mcp_query(
            "sid-1",
            {"query": "Search", "selected_tools": ["mcp_mpo_sharepoint_server"]},
        )
    )
    errors = [data for event, data, _ in emitted if event == "crew-mcp-error"]
    assert len(errors) == 1
    assert "Graph access token is required" in errors[0]["error"]
    assert not any(event == "crew-mcp-result" for event, _, _ in emitted)
    assert received == []
