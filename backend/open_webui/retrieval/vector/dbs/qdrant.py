import asyncio
import inspect
import logging
import threading
from functools import wraps
from typing import Optional

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchValue,
    PointStruct,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

from open_webui.config import (
    QDRANT_API_KEY,
    QDRANT_ENABLE_QUANTIZATION,
    QDRANT_ON_DISK_HNSW,
    QDRANT_ON_DISK_PAYLOAD,
    QDRANT_ON_DISK_VECTOR,
    QDRANT_PREFER_GRPC,
    QDRANT_TIMEOUT_SECONDS,
    QDRANT_URL,
)
from open_webui.env import SRC_LOG_LEVELS
from open_webui.retrieval.vector.locks import get_collection_lock_manager
from open_webui.retrieval.vector.main import GetResult, SearchResult, VectorItem

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def with_qdrant_client(func):
    """Inject the loop-scoped Qdrant client for async instance methods."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        client = await self._get_client()
        return await func(self, client, *args, **kwargs)

    return wrapper


# Global distributed lock manager for collection-level synchronization.
_collection_lock_manager = get_collection_lock_manager()


class QdrantClient:
    def __init__(self):
        # Keep one async client per event loop to avoid closing a client that
        # is still serving requests on another loop.
        self._clients_by_loop: dict[int, AsyncQdrantClient] = {}
        # Loop objects are stored alongside clients so _prune_closed_loops() can
        # call loop.is_closed() and evict entries for loops that have since ended.
        self._loops_by_id: dict[int, asyncio.AbstractEventLoop] = {}
        self._fallback_client: Optional[AsyncQdrantClient] = None
        # NOTE: threading.Lock is intentional here because these sections only do
        # fast synchronous reference swaps. Never add await calls while holding it.
        self._client_lock = threading.Lock()

    def _build_client(self) -> AsyncQdrantClient:
        return AsyncQdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            timeout=int(QDRANT_TIMEOUT_SECONDS),
            prefer_grpc=QDRANT_PREFER_GRPC,
        )

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def _close_client(self, client: Optional[AsyncQdrantClient]) -> None:
        if client is None:
            return

        try:
            close_method = getattr(client, "aclose", None)
            if close_method is not None:
                await self._maybe_await(close_method())
                return

            close_method = getattr(client, "close", None)
            if close_method is not None:
                await self._maybe_await(close_method())
        except Exception as e:
            log.debug(f"Error closing stale Qdrant client: {type(e).__name__}: {e}")

    def _prune_closed_loops(self) -> None:
        """Remove entries for closed event loops. Must be called with _client_lock held.

        Stale clients are closed asynchronously on the current running loop (if any)
        so this method never blocks the caller. On the no-running-loop path the
        client is simply de-referenced and left to the GC.
        """
        stale_ids = [
            loop_id for loop_id, loop in self._loops_by_id.items() if loop.is_closed()
        ]
        if not stale_ids:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        for loop_id in stale_ids:
            client = self._clients_by_loop.pop(loop_id, None)
            self._loops_by_id.pop(loop_id, None)
            if client is not None and current_loop is not None:
                # create_task is synchronous and safe to call while holding the
                # threading.Lock because we are on the running loop's thread.
                current_loop.create_task(self._close_client(client))

        log.info(
            f"Pruned {len(stale_ids)} stale Qdrant client(s) for closed loops: "
            f"remaining_active={len(self._clients_by_loop)}"
        )

    def _get_or_create_client_for_current_loop(self) -> AsyncQdrantClient:
        """Return a client bound to the current asyncio loop.

        Clients are pooled per loop id to avoid cross-loop replacement races.
        """
        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop = None
            current_loop_id = None

        with self._client_lock:
            self._prune_closed_loops()

            if current_loop_id is None:
                if self._fallback_client is None:
                    self._fallback_client = self._build_client()
                return self._fallback_client

            client = self._clients_by_loop.get(current_loop_id)
            if client is not None:
                return client

            client = self._build_client()
            self._clients_by_loop[current_loop_id] = client
            self._loops_by_id[current_loop_id] = current_loop

            if len(self._clients_by_loop) > 1:
                log.info(
                    "Created additional loop-scoped Qdrant client: "
                    f"loop_id={current_loop_id}, active_loops={len(self._clients_by_loop)}"
                )

            return client

    async def _get_client(self) -> AsyncQdrantClient:
        return self._get_or_create_client_for_current_loop()

    @property
    def client(self):
        """
        Compatibility accessor.

        Returns a loop-aware cached client synchronously.
        """
        return self._get_or_create_client_for_current_loop()

    async def close(self):
        """Close all cached loop-scoped clients, if any."""
        with self._client_lock:
            clients = list(self._clients_by_loop.values())
            fallback_client = self._fallback_client
            self._clients_by_loop.clear()
            self._loops_by_id.clear()
            self._fallback_client = None

        if fallback_client is not None:
            clients.append(fallback_client)

        # Close each client once even if structures referenced the same object.
        seen = set()
        for client in clients:
            if id(client) in seen:
                continue
            seen.add(id(client))
            await self._close_client(client)

    @staticmethod
    def _build_filter(filter_data: Optional[dict]) -> Optional[Filter]:
        if not filter_data:
            return None

        conditions = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filter_data.items()
        ]
        return Filter(must=conditions)

    @with_qdrant_client
    async def has_collection(
        self, client: AsyncQdrantClient, collection_name: str
    ) -> bool:
        return await client.collection_exists(collection_name=collection_name)

    @with_qdrant_client
    async def list_collections(self, client: AsyncQdrantClient) -> list[str]:
        collections_response = await client.get_collections()
        return [collection.name for collection in collections_response.collections]

    def _result_to_get_result(self, result) -> GetResult:
        ids = []
        documents = []
        metadatas = []
        # Check if result is valid and has records
        if result and len(result) > 0 and len(result[0]) > 0:
            # Iterate over the tuple of records
            for record in result[0]:
                ids.append([record.id])
                documents.append([record.payload["text"]])
                metadatas.append([record.payload["metadata"]])

        return GetResult(
            **{
                "ids": ids,
                "documents": documents,
                "metadatas": metadatas,
            }
        )

    def _result_to_search_result(self, result) -> SearchResult:
        ids = []
        distances = []
        documents = []
        metadatas = []

        for point in result.points:
            ids.append([point.id])
            distances.append([point.score])
            documents.append([point.payload["text"]])
            metadatas.append([point.payload["metadata"]])

        return SearchResult(
            **{
                "ids": ids,
                "distances": distances,
                "documents": documents,
                "metadatas": metadatas,
            }
        )

    @with_qdrant_client
    async def get_collection_sample_metadata(
        self, client: AsyncQdrantClient, collection_name: str
    ) -> Optional[dict]:
        """Get metadata from a sample point in the collection to check properties like age."""
        try:
            points, _ = await client.scroll(
                collection_name=collection_name,
                limit=1,
                with_payload=True,
            )
            if points and len(points) > 0:
                point = points[0]
                if hasattr(point, "payload") and point.payload:
                    return point.payload.get("metadata", {})
            return None
        except Exception:
            return None

    @with_qdrant_client
    async def _delete_collection_unlocked(
        self, client: AsyncQdrantClient, collection_name: str
    ):
        try:
            result = await client.delete_collection(collection_name=collection_name)
            log.info(f"Deleted collection: {collection_name}")
            return result
        except Exception as e:
            if not await client.collection_exists(collection_name=collection_name):
                return True
            log.error(
                f"Error deleting collection {collection_name}: {type(e).__name__}: {e}"
            )
            raise

    async def delete_collection(self, collection_name: str):
        """
        Delete the collection based on the collection name.
        Uses distributed Redis locking to prevent race conditions across instances.
        """
        async with _collection_lock_manager.acquire_lock(collection_name):
            return await self._delete_collection_unlocked(collection_name)

    @with_qdrant_client
    async def search(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        vectors: list[list[float | int]],
        limit: int,
    ) -> Optional[SearchResult]:
        result = await client.query_points(
            collection_name=collection_name,
            query=vectors,
            limit=limit,
            with_payload=True,
        )
        return self._result_to_search_result(result)

    @with_qdrant_client
    async def query(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        filter: dict,
        limit: Optional[int] = None,
    ) -> Optional[GetResult]:
        try:
            if not await client.collection_exists(collection_name=collection_name):
                return None

            points, _ = await client.scroll(
                collection_name=collection_name,
                scroll_filter=self._build_filter(filter),
                limit=limit or 1,
            )

            return self._result_to_get_result(points)
        except Exception as e:
            log.error(f"Error querying Qdrant collection {collection_name}: {e}")
            return None

    @with_qdrant_client
    async def get(
        self, client: AsyncQdrantClient, collection_name: str
    ) -> Optional[GetResult]:
        points = await client.count(collection_name=collection_name)
        if points.count:
            result = await client.scroll(
                collection_name=collection_name,
                with_payload=True,
                limit=points.count,
            )

            return self._result_to_get_result(result)

        return None

    async def insert(
        self,
        collection_name: str,
        items: list[VectorItem],
    ):
        return await self.upsert(
            collection_name=collection_name,
            items=items,
        )

    async def _ensure_collection_exists(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        items: list[VectorItem],
    ) -> None:
        if await client.collection_exists(collection_name=collection_name):
            return

        quantization_config = (
            ScalarQuantization(
                scalar=ScalarQuantizationConfig(type=ScalarType.INT8, always_ram=True)
            )
            if QDRANT_ENABLE_QUANTIZATION
            else None
        )

        try:
            await client.create_collection(
                collection_name=collection_name,
                on_disk_payload=QDRANT_ON_DISK_PAYLOAD,
                hnsw_config=HnswConfigDiff(on_disk=QDRANT_ON_DISK_HNSW),
                vectors_config=VectorParams(
                    size=len(items[0]["vector"]),
                    distance=Distance.COSINE,
                    on_disk=QDRANT_ON_DISK_VECTOR,
                    multivector_config=models.MultiVectorConfig(
                        comparator=models.MultiVectorComparator.MAX_SIM
                    ),
                    quantization_config=quantization_config,
                ),
            )
            log.info(f"Created new collection: {collection_name}")
        except Exception as e:
            if await client.collection_exists(collection_name=collection_name):
                return
            log.error(
                f"Error creating collection {collection_name}: {type(e).__name__}: {e}"
            )
            raise

    @with_qdrant_client
    async def _upsert_unlocked(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        items: list[VectorItem],
    ):
        await self._ensure_collection_exists(client, collection_name, items)

        points = [
            PointStruct(
                id=item["id"],
                vector=item["vector"],
                payload={"text": item["text"], "metadata": item["metadata"]},
            )
            for item in items
        ]

        # wait=True ensures data is committed before returning.
        return await client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True,
        )

    async def upsert(
        self,
        collection_name: str,
        items: list[VectorItem],
    ):
        """
        Update the items in the collection, if the items are not present, insert them.
        If the collection does not exist, it will be created.

        Uses distributed Redis locking to prevent race conditions across multiple instances.
        """
        async with _collection_lock_manager.acquire_lock(collection_name):
            return await self._upsert_unlocked(collection_name, items)

    @with_qdrant_client
    async def delete(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        ids: Optional[list[str]] = None,
        filter: Optional[dict] = None,
    ):
        if ids:
            selector = ids
        elif filter:
            selector = self._build_filter(filter)
        else:
            raise ValueError("Either ids or filter must be provided for delete()")

        return await client.delete(
            collection_name=collection_name,
            points_selector=selector,
        )

    @with_qdrant_client
    async def reset(self, client: AsyncQdrantClient):
        collection_response = await client.get_collections()

        for collection in collection_response.collections:
            await client.delete_collection(collection_name=collection.name)
