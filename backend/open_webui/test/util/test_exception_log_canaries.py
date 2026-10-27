"""OBS-18: exception text never carries canaries into a log sink.

Each test makes a dependency raise an exception whose message holds canary
prompts, credentials and URL query strings, then checks the harness sinks.
Tracebacks must still show frame locations.
"""

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest
import requests

from open_webui.retrieval import utils as retrieval_utils
from open_webui.utils import middleware

log = logging.getLogger("open_webui.test.exception_canaries")


def _secret_message(canaries) -> str:
    """Text a provider, HTTP client or tool could echo in its error."""
    return (
        f"request for {canaries.prompt} failed: "
        f"https://provider.example/v1/embed?api-key={canaries.api_key}&q=1 "
        f"Authorization: Bearer {canaries.token} user={canaries.email}"
    )


def _frames(harness) -> list[str]:
    return [r.exc_text for r in harness.records if r.exc_text]


@pytest.mark.parametrize(
    "embed, status",
    [
        (retrieval_utils.generate_openai_batch_embeddings, None),
        (retrieval_utils.generate_ollama_batch_embeddings, 401),
    ],
    ids=["openai", "ollama"],
)
def test_batch_embedding_errors_return_none_without_leaking(
    canary_harness, monkeypatch, embed, status
):
    canaries = canary_harness.canaries
    response = types.SimpleNamespace(status_code=status) if status else None

    def failing_post(url, **kwargs):
        raise requests.exceptions.ConnectionError(
            _secret_message(canaries), response=response
        )

    monkeypatch.setattr(retrieval_utils.requests, "post", failing_post)
    result = embed(
        model="embed-model",
        texts=[canaries.prompt],
        url=f"https://provider.example?key={canaries.api_key}",
        key=canaries.api_key,
    )

    assert result is None
    messages = [r.getMessage() for r in canary_harness.records]
    assert any(
        "ConnectionError" in m and f"status={status}" in m for m in messages
    ), messages
    canary_harness.assert_no_canaries()


@pytest.mark.parametrize("operation", ["query_doc", "get_doc"])
def test_vector_db_errors_reraise_without_leaking(
    canary_harness, monkeypatch, operation
):
    canaries = canary_harness.canaries
    error = RuntimeError(_secret_message(canaries))

    class FailingClient:
        async def search(self, **kwargs):
            raise error

        async def get(self, **kwargs):
            raise error

    monkeypatch.setattr(retrieval_utils, "VECTOR_DB_CLIENT", FailingClient())
    if operation == "query_doc":
        call = retrieval_utils.query_doc("collection", [0.1, 0.2], 3)
    else:
        call = retrieval_utils.get_doc("collection")

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(call)

    assert raised.value is error
    assert any(
        f"{operation} failed: RuntimeError" == r.getMessage()
        for r in canary_harness.records
    )
    canary_harness.assert_no_canaries()


def test_web_search_query_generation_failure_falls_back_without_leaking(
    canary_harness, monkeypatch
):
    canaries = canary_harness.canaries
    searched = []

    async def failing_generate_queries(request, form_data, user):
        raise ValueError(_secret_message(canaries))

    async def failing_search(request, form_data, **kwargs):
        searched.append(form_data.query)
        raise ConnectionError(_secret_message(canaries))

    async def emitter(event):
        pass

    monkeypatch.setattr(middleware, "generate_queries", failing_generate_queries)
    monkeypatch.setattr(middleware, "process_web_search", failing_search)
    form_data = {
        "model": "canary-model",
        "messages": [{"role": "user", "content": canaries.prompt}],
    }

    result = asyncio.run(
        middleware.chat_web_search_handler(
            types.SimpleNamespace(), form_data, {"__event_emitter__": emitter}, None
        )
    )

    assert result is form_data
    # The query generation fallback searches for the user message itself.
    assert searched == [canaries.prompt]
    messages = [r.getMessage() for r in canary_harness.records]
    assert "Web search query generation failed: ValueError" in messages
    assert "Web search failed: ConnectionError" in messages
    frames = _frames(canary_harness)
    assert any(", in failing_generate_queries" in text for text in frames)
    assert any(", in failing_search" in text for text in frames)
    assert all("[message omitted]" in text for text in frames)
    # The prompt is in the search request, not in a log sink.
    canary_harness.assert_no_canaries()


@pytest.fixture
def crew_integration(monkeypatch):
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
        Path(__file__).resolve().parents[3]
        / "mcp_backend/integration/crew_mcp_integration.py"
    )
    spec = importlib.util.spec_from_file_location("obs18_crew_integration", source)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "crewai", crewai)
        imports.setitem(sys.modules, "crewai_tools", tools)
        imports.setitem(sys.modules, "mcp", mcp)
        imports.setenv("CREWAI_DISABLE_TELEMETRY", "true")
        imports.setenv("CREWAI_TRACING_ENABLED", "false")
        spec.loader.exec_module(module)
    return module


def test_mcp_tool_error_is_logged_by_type_only(
    canary_harness, monkeypatch, crew_integration, tmp_path
):
    canaries = canary_harness.canaries
    server = tmp_path / "sharepoint.py"
    server.touch()
    manager = crew_integration.CrewMCPManager.__new__(crew_integration.CrewMCPManager)
    manager._sharepoint_server_paths = {"MPO": server}
    manager.get_azure_llm_config = lambda: None

    class Adapter:
        def __init__(self, params):
            pass

        def __enter__(self):
            return [types.SimpleNamespace(name="mpo_search_documents_fast")]

        def __exit__(self, *args):
            pass

    class Crew:
        def __init__(self, **kwargs):
            pass

        def kickoff(self):
            raise RuntimeError(
                f"tool mpo_search_documents_fast({_secret_message(canaries)})"
            )

    monkeypatch.setenv("SHP_USE_DELEGATED_ACCESS", "true")
    monkeypatch.setattr(crew_integration, "MCPServerAdapter", Adapter)
    monkeypatch.setattr(crew_integration, "Crew", Crew)

    answer, usage = manager.run_sharepoint_crew("MPO", canaries.prompt, canaries.token)

    # The user still gets the tool error in the chat answer; it is not logged.
    assert "I encountered an issue" in answer and usage == {}
    assert any(
        r.getMessage() == "Error in CrewAI MCP MPO SharePoint integration: RuntimeError"
        for r in canary_harness.records
    )
    canary_harness.assert_no_canaries()


def test_database_write_error_hides_bound_parameters(canary_harness):
    from sqlalchemy import Column, MetaData, String, Table, create_engine, exc

    from open_webui.internal.db import ENGINE_OPTIONS

    canaries = canary_harness.canaries
    engine = create_engine("sqlite://", **ENGINE_OPTIONS)
    table = Table(
        "chat",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("body", String),
    )
    table.metadata.create_all(engine)
    row = {"id": "chat-1", "body": canaries.prompt}
    with engine.begin() as conn:
        conn.execute(table.insert(), row)

    try:
        with engine.begin() as conn:
            conn.execute(table.insert(), {**row, "body": canaries.api_key})
    except exc.IntegrityError as error:
        assert canaries.api_key not in str(error)
        assert "hidden due to hide_parameters" in str(error)
        log.exception("Error inserting chat")
    else:
        pytest.fail("duplicate insert did not raise")

    frames = _frames(canary_harness)
    assert any("sqlalchemy.exc.IntegrityError: [message omitted]" in t for t in frames)
    assert any(
        ", in test_database_write_error_hides_bound_parameters" in t for t in frames
    )
    canary_harness.assert_no_canaries()


def test_provider_error_logged_with_exception_object_is_type_only(canary_harness):
    canaries = canary_harness.canaries

    def call_provider():
        raise RuntimeError(_secret_message(canaries))

    try:
        call_provider()
    except RuntimeError as e:
        # The pre-OBS-18 call-site patterns that rely on the source filter.
        log.exception(e)
        log.error("Provider call failed: %s", e)
        log.error("Provider call failed", exc_info=True)

    messages = [r.getMessage() for r in canary_harness.records]
    assert messages == [
        "RuntimeError",
        "Provider call failed: RuntimeError",
        "Provider call failed",
    ]
    assert all(", in call_provider" in text for text in _frames(canary_harness))
    canary_harness.assert_no_canaries()
