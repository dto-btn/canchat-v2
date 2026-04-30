"""Unit tests for loop-scoped Qdrant client pruning behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("qdrant_client")

import open_webui.retrieval.vector.dbs.qdrant as qdrant_module
from open_webui.retrieval.vector.dbs.qdrant import QdrantClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeLoop:
    def __init__(self, closed: bool):
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


@pytest.mark.anyio
async def test_get_or_create_client_prunes_closed_loop_entries(monkeypatch):
    """A closed loop entry should be removed and its client closed on the active loop."""
    client = QdrantClient()

    stale_loop_id = 12345
    stale_client = MagicMock(name="stale_client")
    new_client = MagicMock(name="new_client")

    client._clients_by_loop[stale_loop_id] = stale_client
    client._loops_by_id[stale_loop_id] = _FakeLoop(closed=True)

    close_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(client, "_close_client", close_mock)
    monkeypatch.setattr(client, "_build_client", MagicMock(return_value=new_client))

    current_loop = asyncio.get_running_loop()
    current_loop_id = id(current_loop)

    got = client._get_or_create_client_for_current_loop()

    assert got is new_client
    assert stale_loop_id not in client._clients_by_loop
    assert stale_loop_id not in client._loops_by_id
    assert client._clients_by_loop[current_loop_id] is new_client
    assert client._loops_by_id[current_loop_id] is current_loop

    # Allow create_task-scheduled close coroutine to execute.
    await asyncio.sleep(0)
    close_mock.assert_awaited_once_with(stale_client)


def test_get_or_create_client_no_running_loop_prunes_without_scheduling_close(
    monkeypatch,
):
    """When no event loop is running, stale entries are evicted and fallback client is used."""
    client = QdrantClient()

    stale_loop_id = 67890
    stale_client = MagicMock(name="stale_client")
    fallback_client = MagicMock(name="fallback_client")

    client._clients_by_loop[stale_loop_id] = stale_client
    client._loops_by_id[stale_loop_id] = _FakeLoop(closed=True)

    close_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(client, "_close_client", close_mock)
    monkeypatch.setattr(
        client, "_build_client", MagicMock(return_value=fallback_client)
    )
    monkeypatch.setattr(
        qdrant_module.asyncio,
        "get_running_loop",
        MagicMock(side_effect=RuntimeError("no running loop")),
    )

    got = client._get_or_create_client_for_current_loop()

    assert got is fallback_client
    assert client._fallback_client is fallback_client
    assert client._clients_by_loop == {}
    assert client._loops_by_id == {}
    close_mock.assert_not_called()
