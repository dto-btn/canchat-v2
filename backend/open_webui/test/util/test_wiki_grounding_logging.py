"""OBS-19: Wikipedia grounding logs must not reveal the query or result titles."""

import asyncio
import hashlib
import logging
from datetime import datetime

import pytest

from open_webui.grounding import wiki_search_utils
from open_webui.grounding.wiki_search_utils import WikiSearchGrounder


class _FakeEmbeddings:
    def __init__(self, results):
        self._results = results

    def search(self, query, limit):
        return list(self._results)


def _grounder(results, reranker=None):
    grounder = WikiSearchGrounder()
    grounder._initialized = True
    grounder.model_loaded = True
    grounder.embeddings = _FakeEmbeddings(results)
    if reranker is not None:
        grounder.reranker = reranker
        grounder.reranker_loaded = True
    return grounder


@pytest.fixture(autouse=True)
def _fresh_wiki_locks(monkeypatch):
    # Class-level asyncio primitives must not outlive one test's event loop.
    monkeypatch.setattr(WikiSearchGrounder, "_semaphore", None)
    monkeypatch.setattr(WikiSearchGrounder, "_semaphore_lock", asyncio.Lock())
    monkeypatch.setattr(WikiSearchGrounder, "_query_locks_lock", asyncio.Lock())


@pytest.mark.parametrize("use_reranker", [True, False])
def test_wiki_grounding_logs_omit_query_and_titles(canary_harness, use_reranker):
    canaries = canary_harness.canaries
    canary_harness.set_level(wiki_search_utils.log, logging.DEBUG)
    year = str(datetime.now().year)
    tag = canaries.tag

    query = canaries.add("query", f"{tag} canaryquery about {year} {tag}")
    title = canaries.add("title", f"Canarytitle {tag} {year}")
    other_title = canaries.add("other title", f"Othercanary {tag} {year}")
    query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
    canaries.add("query hash", query_hash, (query_hash[:8],))

    results = [
        {"id": title, "text": "body one", "score": 0.80},
        {"id": other_title, "text": "body two", "score": 0.78},
    ]
    reranker = None
    if use_reranker:
        reranker = lambda q, limit: [  # noqa: E731
            {"id": title, "score": 0.9},
            {"id": other_title, "score": 0.5},
        ]

    grounder = _grounder(results, reranker)
    grounding = asyncio.run(grounder.ground_query(query))

    assert grounding is not None
    returned = grounding["grounding_data"]
    assert {r["title"] for r in returned} == {title, other_title}
    assert all(r["temporal_boost_applied"] for r in returned)

    messages = [r.getMessage() for r in canary_harness.records]
    assert any("Boosted result #" in m for m in messages)
    if use_reranker:
        assert any("top score:" in m for m in messages)
    assert any("query lock" in m for m in messages)
    canary_harness.assert_no_canaries()
