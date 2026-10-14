import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from open_webui.grounding import ekh_client
from open_webui.grounding.wiki_search_utils import WikiSearchGrounder


def _fake_config(provider="ekh", knowledge_source="wikipedia"):
    return SimpleNamespace(
        WIKIPEDIA_GROUNDING_PROVIDER=provider,
        EKH_BASE_URL="http://ekh.test",
        EKH_KNOWLEDGE_SOURCE=knowledge_source,
        EKH_API_KEY="",
        EKH_TIMEOUT_SECONDS=5.0,
    )


def _row(pid, source, name, similarity, chunk=0):
    lang = {"enwiki": "en", "frwiki": "fr"}[source]
    return {
        "id": pid,
        "source": source,
        "name": name,
        "content": f"{name} chunk {chunk}",
        "chunk_index": chunk,
        "similarity": similarity,
        "language": lang,
        "url": f"https://{lang}.wikipedia.org/?curid={pid}",
    }


# ── ekh_client ───────────────────────────────────────────────────────────────


async def _serve(handler):
    app = web.Application()
    app.router.add_get("/database/wikipedia/search", handler)
    app.router.add_get("/database/tbs-policies/search", handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_client_returns_results_and_sends_params():
    seen = {}

    async def handler(request):
        seen["query"] = dict(request.query)
        seen["api_key"] = request.headers.get("X-API-Key")
        return web.json_response({"results": [_row(1, "enwiki", "Ottawa", 0.9)]})

    server = await _serve(handler)
    try:
        rows = await ekh_client.search_wikipedia(
            "ottawa", base_url=str(server.make_url("/")), limit=7, api_key="k"
        )
    finally:
        await server.close()

    assert rows[0]["name"] == "Ottawa"
    assert seen["query"] == {"query": "ottawa", "limit": "7"}
    assert seen["api_key"] == "k"


@pytest.mark.asyncio
async def test_client_omits_api_key_header_when_unset():
    seen = {}

    async def handler(request):
        seen["has_key"] = "X-API-Key" in request.headers
        return web.json_response({"results": []})

    server = await _serve(handler)
    try:
        await ekh_client.search_wikipedia("x", base_url=str(server.make_url("/")))
    finally:
        await server.close()

    assert seen["has_key"] is False


@pytest.mark.asyncio
async def test_client_returns_empty_on_http_error():
    async def handler(request):
        return web.json_response({"detail": "boom"}, status=503)

    server = await _serve(handler)
    try:
        rows = await ekh_client.search_wikipedia(
            "x", base_url=str(server.make_url("/"))
        )
    finally:
        await server.close()

    assert rows == []


@pytest.mark.asyncio
async def test_client_returns_empty_on_malformed_payload():
    async def handler(request):
        return web.json_response(["not", "a", "dict"])

    server = await _serve(handler)
    try:
        rows = await ekh_client.search_wikipedia(
            "x", base_url=str(server.make_url("/"))
        )
    finally:
        await server.close()

    assert rows == []


@pytest.mark.asyncio
async def test_client_returns_empty_on_timeout():
    import asyncio

    async def handler(request):
        await asyncio.sleep(1)
        return web.json_response({"results": []})

    server = await _serve(handler)
    try:
        rows = await ekh_client.search_wikipedia(
            "x", base_url=str(server.make_url("/")), timeout_seconds=0.1
        )
    finally:
        await server.close()

    assert rows == []


@pytest.mark.asyncio
async def test_client_returns_empty_when_unreachable_or_unconfigured():
    assert await ekh_client.search_wikipedia("x", base_url="") == []
    assert (
        await ekh_client.search_wikipedia(
            "x", base_url="http://127.0.0.1:1", timeout_seconds=1
        )
        == []
    )


@pytest.mark.asyncio
async def test_client_uses_configured_knowledge_source_route():
    seen = {}

    async def handler(request):
        seen["path"] = request.path
        return web.json_response({"results": []})

    server = await _serve(handler)
    try:
        await ekh_client.search_wikipedia(
            "leave policy",
            base_url=str(server.make_url("/")),
            knowledge_source="tbs-policies",
        )
    finally:
        await server.close()

    assert seen["path"] == "/database/tbs-policies/search"


# ── WikiSearchGrounder with the EKH provider ─────────────────────────────────


@pytest.mark.asyncio
async def test_ekh_search_dedupes_chunks_and_maps_fields():
    rows = [
        _row(3, "frwiki", "Antoine Meillet", 0.39, chunk=6),
        _row(10, "frwiki", "Algorithmique", 0.37, chunk=1),
        _row(3, "frwiki", "Antoine Meillet", 0.41, chunk=7),
        _row(3, "enwiki", "Antoine Meillet", 0.30),
    ]
    with (
        patch.dict(sys.modules, {"open_webui.config": _fake_config()}),
        patch.object(ekh_client, "search_wikipedia", AsyncMock(return_value=rows)),
    ):
        results = await WikiSearchGrounder().search("history")

    assert [(r["title"], r["language"]) for r in results] == [
        ("Antoine Meillet", "fr"),
        ("Algorithmique", "fr"),
        ("Antoine Meillet", "en"),
    ]
    top = results[0]
    assert top["score"] == 0.41
    assert top["content"] == "Antoine Meillet chunk 7"
    assert top["url"] == "https://fr.wikipedia.org/?curid=3"
    assert top["source"] == "ekh-wikipedia"


@pytest.mark.asyncio
async def test_ekh_search_returns_empty_when_ekh_fails():
    with (
        patch.dict(sys.modules, {"open_webui.config": _fake_config()}),
        patch.object(ekh_client, "search_wikipedia", AsyncMock(return_value=[])),
    ):
        grounder = WikiSearchGrounder()
        assert await grounder.search("history") == []
        assert await grounder.ground_query("history") is None


@pytest.mark.asyncio
async def test_ekh_provider_skips_txtai_loading():
    grounder = WikiSearchGrounder()
    with (
        patch.dict(sys.modules, {"open_webui.config": _fake_config()}),
        patch.object(grounder, "_load_txtai_model") as load_txtai,
    ):
        assert await grounder.initialize() is True

    load_txtai.assert_not_called()


@pytest.mark.asyncio
async def test_ground_query_labels_ekh_source_in_context():
    rows = [_row(1, "enwiki", "Ottawa", 0.8)]
    with (
        patch.dict(sys.modules, {"open_webui.config": _fake_config()}),
        patch.object(ekh_client, "search_wikipedia", AsyncMock(return_value=rows)),
    ):
        grounder = WikiSearchGrounder()
        data = await grounder.ground_query("capital of canada")

    assert data["source"] == "ekh-wikipedia"
    assert "Source: ekh-wikipedia" in grounder.format_grounding_context(data)


@pytest.mark.asyncio
async def test_tbs_policies_source_maps_policy_citation():
    row = {
        "id": 42,
        "source": "tbs-policies",
        "name": "Values and Ethics Code",
        "content": "Policy text",
        "chunk_index": 0,
        "similarity": 0.9,
    }
    with (
        patch.dict(
            sys.modules,
            {"open_webui.config": _fake_config(knowledge_source="tbs-policies")},
        ),
        patch.object(
            ekh_client, "search_wikipedia", AsyncMock(return_value=[row])
        ) as search,
    ):
        data = await WikiSearchGrounder().ground_query("values and ethics")

    assert search.await_args.kwargs["knowledge_source"] == "tbs-policies"
    assert data["grounding_data"][0]["url"] == (
        "https://www.tbs-sct.canada.ca/pol/doc-eng.aspx?id=42"
    )
    assert data["grounding_data"][0]["source"] == "ekh-tbs-policies"
    assert data["grounding_data"][0]["language"] is None
    assert data["source"] == "ekh-tbs-policies"
    assert "Source: ekh-tbs-policies" in WikiSearchGrounder().format_grounding_context(
        data
    )


@pytest.mark.asyncio
async def test_txtai_provider_does_not_call_ekh():
    grounder = WikiSearchGrounder()
    with (
        patch.dict(sys.modules, {"open_webui.config": _fake_config(provider="txtai")}),
        patch.object(ekh_client, "search_wikipedia", AsyncMock()) as ekh_search,
        patch.object(grounder, "ensure_initialized", AsyncMock(return_value=False)),
    ):
        assert await grounder.search("history") == []

    ekh_search.assert_not_called()
