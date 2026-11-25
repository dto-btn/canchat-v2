import json
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
