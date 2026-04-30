"""Pytest integration tests for concurrent lock + vector DB behavior."""

import asyncio
import os
import time
import uuid

import pytest

pytest.importorskip("qdrant_client")
from qdrant_client.http.exceptions import UnexpectedResponse

# Development-friendly defaults for local runs.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from open_webui.retrieval.vector.dbs.qdrant import QdrantClient
from open_webui.retrieval.vector.locks import get_collection_lock_manager


def _make_vector(base: float = 0.1, length: int = 8) -> list[float]:
    return [base + i * 0.01 for i in range(length)]


def _make_document(doc_id: str, text: str, metadata: dict | None = None) -> dict:
    return {
        "id": doc_id,
        "text": text,
        "vector": _make_vector(base=hash(doc_id) % 10),
        "metadata": metadata or {"origin": "concurrent-test"},
    }


async def _safe_delete_collection(client: QdrantClient, collection_name: str) -> None:
    try:
        await client.delete_collection(collection_name=collection_name)
    except Exception:
        # Cleanup should never fail the suite.
        pass


async def _collect_document_ids(
    client: QdrantClient, collection_name: str
) -> list[str]:
    try:
        qdrant_client = await client._get_client()
        points, _ = await qdrant_client.scroll(
            collection_name=collection_name,
            with_payload=False,
            limit=200,
        )
    except UnexpectedResponse as exc:
        if "Not found" in str(exc):
            return []
        raise
    return [str(point.id) for point in points]


async def _search_for_document(
    client: QdrantClient,
    collection_name: str,
    target_id: str,
    vector: list[float],
) -> bool:
    try:
        result = await client.search(
            collection_name=collection_name,
            vectors=[vector],
            limit=1,
        )
    except UnexpectedResponse as exc:
        if "Not found" in str(exc):
            return False
        raise

    if result and result.ids:
        for row in result.ids:
            if target_id in row:
                return True
    return False


async def _upload_document(
    manager,
    client: QdrantClient,
    collection_name: str,
    doc_id: str,
    work_delay: float,
    active_counter: list[int],
    counter_lock: asyncio.Lock,
) -> str:
    async with manager.acquire_lock(collection_name, timeout_secs=60):
        async with counter_lock:
            active_counter[0] += 1
            if active_counter[0] > 1:
                raise RuntimeError("Lock was not exclusive")

        await client.upsert(
            collection_name=collection_name,
            items=[_make_document(doc_id, f"text-{doc_id}")],
        )

        await asyncio.sleep(work_delay)

        async with counter_lock:
            active_counter[0] -= 1

    return doc_id


async def _retrieval_worker(
    client: QdrantClient,
    collection_name: str,
    target_id: str,
    vector: list[float],
    timeout: float = 16.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _search_for_document(client, collection_name, target_id, vector):
            return True
        await asyncio.sleep(0.25)
    return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def qdrant_client():
    client = QdrantClient()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture(autouse=True)
async def require_integration_services(qdrant_client):
    """Skip module tests when Redis/Qdrant integration services are unavailable."""
    manager = get_collection_lock_manager()
    try:
        if not await manager.health_check(retries=1):
            pytest.skip("Redis is unavailable for concurrency integration tests")
        await qdrant_client.list_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant/Redis integration services unavailable: {exc}")


@pytest.mark.anyio
async def test_concurrent_uploads_same_collection(qdrant_client):
    """Ensures same-collection uploads are serialized by the distributed lock."""
    manager = get_collection_lock_manager()
    collection_name = f"concurrent-upload-{uuid.uuid4().hex[:8]}"

    await _safe_delete_collection(qdrant_client, collection_name)
    active_counter = [0]
    counter_lock = asyncio.Lock()

    try:
        work_delays = [0.8, 0.6, 0.4]
        doc_ids = [str(uuid.uuid4()) for _ in range(3)]

        tasks = [
            asyncio.create_task(
                _upload_document(
                    manager,
                    qdrant_client,
                    collection_name,
                    doc_ids[i],
                    work_delays[i % len(work_delays)],
                    active_counter,
                    counter_lock,
                )
            )
            for i in range(len(doc_ids))
        ]

        await asyncio.gather(*tasks)

        ids = await _collect_document_ids(qdrant_client, collection_name)
        assert len(ids) == len(tasks)
        assert active_counter[0] == 0
    finally:
        await _safe_delete_collection(qdrant_client, collection_name)


@pytest.mark.anyio
async def test_concurrent_upload_and_retrieve(qdrant_client):
    """Validates readers can observe committed data during concurrent upload flow."""
    manager = get_collection_lock_manager()
    collection_name = f"concurrent-upload-retrieve-{uuid.uuid4().hex[:8]}"

    await _safe_delete_collection(qdrant_client, collection_name)

    target_id = str(uuid.uuid4())
    target_vector = _make_vector(base=hash(target_id) % 10)

    async def long_upload():
        async with manager.acquire_lock(collection_name, timeout_secs=60):
            await qdrant_client.upsert(
                collection_name=collection_name,
                items=[_make_document(target_id, "retrieval-text")],
            )
            await asyncio.sleep(1.2)

    try:
        retrieval_tasks = [
            asyncio.create_task(
                _retrieval_worker(
                    qdrant_client,
                    collection_name,
                    target_id,
                    target_vector,
                )
            )
            for _ in range(2)
        ]
        upload_task = asyncio.create_task(long_upload())

        results = await asyncio.gather(upload_task, *retrieval_tasks)
        assert any(results[1:])
    finally:
        await _safe_delete_collection(qdrant_client, collection_name)


@pytest.mark.anyio
async def test_concurrent_delete_collection_same_collection(qdrant_client):
    """Checks concurrent collection deletes are idempotent and error-free."""
    collection_name = f"concurrent-delete-{uuid.uuid4().hex[:8]}"
    await _safe_delete_collection(qdrant_client, collection_name)

    try:
        docs = [_make_document(str(uuid.uuid4()), f"doc-{i}") for i in range(3)]
        await qdrant_client.upsert(collection_name=collection_name, items=docs)

        tasks = [
            asyncio.create_task(qdrant_client.delete_collection(collection_name))
            for _ in range(3)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, Exception)]

        assert not errors
        assert not await qdrant_client.has_collection(collection_name)
    finally:
        await _safe_delete_collection(qdrant_client, collection_name)


@pytest.mark.anyio
async def test_concurrent_upsert_and_delete_collection_are_serialized(qdrant_client):
    """Verifies delete waits behind in-flight upsert work on the same collection lock."""
    manager = get_collection_lock_manager()
    collection_name = f"concurrent-upsert-delete-{uuid.uuid4().hex[:8]}"
    doc_id = str(uuid.uuid4())
    delete_elapsed = {"seconds": 0.0}

    await _safe_delete_collection(qdrant_client, collection_name)

    async def long_upsert_holding_lock():
        async with manager.acquire_lock(collection_name, timeout_secs=60):
            # Reentrant acquisition inside upsert should remain safe.
            await qdrant_client.upsert(
                collection_name=collection_name,
                items=[_make_document(doc_id, "upsert-before-delete")],
            )
            await asyncio.sleep(0.9)

    async def delete_worker():
        await asyncio.sleep(0.1)
        start = time.monotonic()
        await qdrant_client.delete_collection(collection_name)
        delete_elapsed["seconds"] = time.monotonic() - start

    try:
        await asyncio.gather(
            asyncio.create_task(long_upsert_holding_lock()),
            asyncio.create_task(delete_worker()),
        )

        # Delete should block behind the lock held by the upsert task.
        assert delete_elapsed["seconds"] >= 0.6
        assert not await qdrant_client.has_collection(collection_name)
    finally:
        await _safe_delete_collection(qdrant_client, collection_name)


@pytest.mark.anyio
async def test_concurrent_locks_are_collection_scoped(qdrant_client):
    """Confirms different collections can proceed concurrently (no global lock contention)."""
    manager = get_collection_lock_manager()
    collection_a = f"concurrent-scope-a-{uuid.uuid4().hex[:8]}"
    collection_b = f"concurrent-scope-b-{uuid.uuid4().hex[:8]}"
    active_count = [0]
    peak_count = [0]
    count_lock = asyncio.Lock()

    await _safe_delete_collection(qdrant_client, collection_a)
    await _safe_delete_collection(qdrant_client, collection_b)

    async def upload_with_outer_lock(collection_name: str, doc_id: str):
        async with manager.acquire_lock(collection_name, timeout_secs=60):
            async with count_lock:
                active_count[0] += 1
                peak_count[0] = max(peak_count[0], active_count[0])

            # Reentrant acquisition inside upsert should only affect the same collection lock.
            await qdrant_client.upsert(
                collection_name=collection_name,
                items=[_make_document(doc_id, f"text-{doc_id}")],
            )
            await asyncio.sleep(0.6)

            async with count_lock:
                active_count[0] -= 1

    try:
        await asyncio.gather(
            asyncio.create_task(
                upload_with_outer_lock(collection_a, str(uuid.uuid4()))
            ),
            asyncio.create_task(
                upload_with_outer_lock(collection_b, str(uuid.uuid4()))
            ),
        )

        ids_a = await _collect_document_ids(qdrant_client, collection_a)
        ids_b = await _collect_document_ids(qdrant_client, collection_b)

        assert len(ids_a) == 1
        assert len(ids_b) == 1
        assert peak_count[0] == 2
    finally:
        await _safe_delete_collection(qdrant_client, collection_a)
        await _safe_delete_collection(qdrant_client, collection_b)
