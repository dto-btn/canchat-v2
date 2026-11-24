from types import SimpleNamespace

import pytest


@pytest.fixture
def crew_mcp_module(monkeypatch):
    from mcp_backend.integration import crew_mcp_integration

    monkeypatch.setattr(
        crew_mcp_integration.AzureConfig, "validate_config", lambda self: True
    )
    return crew_mcp_integration


def test_adapters_start_lazily_and_close_their_context_managers(
    crew_mcp_module, monkeypatch
):
    created_adapters = []

    class FakeAdapter:
        def __init__(self, _params):
            self.exit_count = 0
            created_adapters.append(self)

        def __enter__(self):
            return [SimpleNamespace(name="tool", description="description")]

        def __exit__(self, *_exc_info):
            self.exit_count += 1

    monkeypatch.setattr(crew_mcp_module, "MCPServerAdapter", FakeAdapter)
    manager = crew_mcp_module.CrewMCPManager()

    assert created_adapters == []

    assert len(manager.get_available_tools()) == 2
    assert len(manager.get_available_tools()) == 2
    assert len(created_adapters) == 2

    manager.cleanup_mcp_adapters()
    assert [adapter.exit_count for adapter in created_adapters] == [1, 1]


def test_sharepoint_adapter_closes_when_crew_execution_fails(
    crew_mcp_module, monkeypatch, tmp_path
):
    adapters = []

    class FakeAdapter:
        def __init__(self, _params):
            self.exit_count = 0
            adapters.append(self)

        def __enter__(self):
            return [SimpleNamespace(name="sharepoint_tool", description="description")]

        def __exit__(self, *_exc_info):
            self.exit_count += 1

    class FailingCrew:
        def __init__(self, **_kwargs):
            pass

        def kickoff(self):
            raise RuntimeError("simulated crew failure")

    monkeypatch.setattr(crew_mcp_module, "MCPServerAdapter", FakeAdapter)
    monkeypatch.setattr(crew_mcp_module, "Agent", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Task", lambda **_kwargs: object())
    monkeypatch.setattr(crew_mcp_module, "Crew", FailingCrew)

    server_path = tmp_path / "sharepoint_server.py"
    server_path.touch()
    manager = crew_mcp_module.CrewMCPManager()
    manager._sharepoint_server_paths = {"MPO": server_path}
    monkeypatch.setattr(manager, "get_azure_llm_config", lambda: object())

    result, _usage = manager.run_sharepoint_crew("MPO", "test query")

    assert "simulated crew failure" in result
    assert len(adapters) == 1
    assert adapters[0].exit_count == 1
