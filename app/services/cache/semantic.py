"""
app/services/cache/semantic.py

L2 Semantic similarity cache.

Strategy:
  - Embed the query using the same embedding model as retrieval
  - Store {embedding, response} pairs in Redis
  - On lookup, compare query embedding against stored embeddings
  - If cosine similarity > threshold (default 0.95), return cached response

This catches paraphrased queries:
  "What is RAG?" ≈ "Explain RAG to me" ≈ "Can you describe RAG?"

Implementation:
  - Embeddings stored as binary blobs in Redis (compact)
  - Small index per org (typically < 10K entries)
  - Linear scan for similarity (fast enough for < 10K entries)
  - For larger scale, use a dedicated vector index (e.g. Qdrant collection)

Cache key structure:
  - Index set: "quillflow:cache:semantic:{org_id}:index" → set of entry IDs
  - Entry: "quillflow:cache:semantic:{org_id}:entry:{entry_id}" → hash with
    embedding (binary), response (JSON), document_ids (JSON)
"""

from __future__ import annotations

import json
import struct
from uuid import UUID, uuid4

import numpy as np
import structlog
from redis.asyncio import Redis

from config import get_settings

logger = structlog.get_logger(__name__)

_PREFIX = "quillflow:cache:semantic"


def _embedding_to_bytes(embedding: list[float]) -> bytes:
    """Pack a float list into compact binary format."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _bytes_to_embedding(data: bytes) -> list[float]:
    """Unpack binary format back to float list."""
    count = len(data) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Both vectors should be normalized (which our embedder does).
    For normalized vectors, cosine similarity = dot product.
    """
    arr_a = np.array(a, dtype=np.float32)
    arr_b = np.array(b, dtype=np.float32)

    dot = np.dot(arr_a, arr_b)
    # Clamp to [-1, 1] to handle floating point errors
    return float(np.clip(dot, -1.0, 1.0))


class SemanticCache:
    """
    L2 semantic similarity cache backed by Redis.

    Catches paraphrased queries by comparing embedding similarity.

    Usage:
        cache = SemanticCache(redis_client, embedder)

        # Check cache
        hit = await cache.get(
            query="What is RAG?",
            query_embedding=[0.1, 0.2, ...],
            org_id=uuid,
        )

        # Cache a response
        await cache.set(
            query="What is RAG?",
            query_embedding=[0.1, 0.2, ...],
            org_id=uuid,
            response=response_dict,
            document_ids=[doc1_id],
        )
    """

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._settings = get_settings()

    @property
    def is_available(self) -> bool:
        return self._redis is not None

    async def get(
        self,
        query_embedding: list[float],
        org_id: UUID,
    ) -> dict | None:
        """
        Search for a semantically similar cached query.

        Args:
            query_embedding: Embedding of the current query
            org_id: Organization UUID

        Returns:
            Cached response dict if similarity > threshold, None otherwise
        """
        if not self.is_available:
            return None

        threshold = self._settings.semantic_cache_threshold
        index_key = f"{_PREFIX}:{org_id}:index"

        try:
            # Get all entry IDs for this org
            entry_ids = await self._redis.smembers(index_key)

            if not entry_ids:
                logger.debug("semantic_cache_empty", org_id=str(org_id)[:8])
                return None

            # Score each entry against the query embedding
            best_score = 0.0
            best_response = None

            # Batch fetch all entries using pipeline
            pipeline = self._redis.pipeline()
            for entry_id in entry_ids:
                entry_key = f"{_PREFIX}:{org_id}:entry:{entry_id.decode() if isinstance(entry_id, bytes) else entry_id}"
                pipeline.hgetall(entry_key)

            results = await pipeline.execute()

            for entry_data in results:
                if not entry_data:
                    continue

                # Extract embedding
                raw_embedding = entry_data.get(b"embedding")
                if raw_embedding is None:
                    continue

                cached_embedding = _bytes_to_embedding(raw_embedding)

                # Compute similarity
                similarity = _cosine_similarity(query_embedding, cached_embedding)

                if similarity > best_score:
                    best_score = similarity
                    if similarity >= threshold:
                        raw_response = entry_data.get(b"response")
                        if raw_response:
                            best_response = json.loads(raw_response)

            if best_response is not None:
                logger.info(
                    "semantic_cache_hit",
                    org_id=str(org_id)[:8],
                    similarity=round(best_score, 4),
                    threshold=threshold,
                    candidates_checked=len(entry_ids),
                )
                return best_response

            logger.debug(
                "semantic_cache_miss",
                org_id=str(org_id)[:8],
                best_similarity=round(best_score, 4),
                threshold=threshold,
                candidates_checked=len(entry_ids),
            )
            return None

        except Exception as e:
            logger.warning("semantic_cache_get_error", error=str(e))
            return None

    async def set(
        self,
        query_embedding: list[float],
        org_id: UUID,
        response: dict,
        document_ids: list[UUID] | None = None,
    ) -> bool:
        """
        Cache a response with its query embedding.

        Args:
            query_embedding: Embedding of the query
            org_id: Organization UUID
            response: Response dict to cache
            document_ids: Source document IDs (for invalidation)

        Returns:
            True if cached successfully
        """
        if not self.is_available:
            return False

        entry_id = str(uuid4())
        index_key = f"{_PREFIX}:{org_id}:index"
        entry_key = f"{_PREFIX}:{org_id}:entry:{entry_id}"
        ttl = self._settings.cache_ttl_seconds

        try:
            pipeline = self._redis.pipeline()

            # Store the entry as a Redis hash
            pipeline.hset(entry_key, mapping={
                "embedding": _embedding_to_bytes(query_embedding),
                "response": json.dumps(response, default=str),
                "document_ids": json.dumps(
                    [str(d) for d in document_ids] if document_ids else []
                ),
            })
            pipeline.expire(entry_key, ttl)

            # Add entry ID to the org's index set
            pipeline.sadd(index_key, entry_id)
            pipeline.expire(index_key, ttl)

            await pipeline.execute()

            logger.debug(
                "semantic_cache_set",
                org_id=str(org_id)[:8],
                entry_id=entry_id[:8],
                ttl=ttl,
            )

            return True

        except Exception as e:
            logger.warning("semantic_cache_set_error", error=str(e))
            return False

    async def invalidate_by_document(
        self,
        document_id: UUID,
        org_id: UUID,
    ) -> int:
        """
        Invalidate semantic cache entries that used a specific document.

        Scans all entries for the org and removes those that reference
        the given document_id.

        Returns:

```python
            Number of entries invalidated
        """
        if not self.is_available:
            return 0

        index_key = f"{_PREFIX}:{org_id}:index"

        try:
            entry_ids = await self._redis.smembers(index_key)
            if not entry_ids:
                return 0

            doc_id_str = str(document_id)
            to_delete = []

            # Check each entry's document dependencies
            for raw_entry_id in entry_ids:
                entry_id = raw_entry_id.decode() if isinstance(raw_entry_id, bytes) else raw_entry_id
                entry_key = f"{_PREFIX}:{org_id}:entry:{entry_id}"

                raw_doc_ids = await self._redis.hget(entry_key, "document_ids")
                if raw_doc_ids:
                    cached_doc_ids = json.loads(raw_doc_ids)
                    if doc_id_str in cached_doc_ids:
                        to_delete.append((entry_id, entry_key))

            if not to_delete:
                return 0

            # Delete matching entries
            pipeline = self._redis.pipeline()
            for entry_id, entry_key in to_delete:
                pipeline.delete(entry_key)
                pipeline.srem(index_key, entry_id)
            await pipeline.execute()

            logger.info(
                "semantic_cache_invalidated_by_document",
                document_id=str(document_id),
                org_id=str(org_id)[:8],
                invalidated_count=len(to_delete),
            )

            return len(to_delete)

        except Exception as e:
            logger.warning("semantic_cache_invalidation_error", error=str(e))
            return 0

    async def invalidate_by_org(self, org_id: UUID) -> int:
        """
        Flush all semantic cache entries for an organization.

        Returns:
            Number of entries deleted
        """
        if not self.is_available:
            return 0

        index_key = f"{_PREFIX}:{org_id}:index"

        try:
            entry_ids = await self._redis.smembers(index_key)
            if not entry_ids:
                return 0

            pipeline = self._redis.pipeline()
            for raw_entry_id in entry_ids:
                entry_id = raw_entry_id.decode() if isinstance(raw_entry_id, bytes) else raw_entry_id
                entry_key = f"{_PREFIX}:{org_id}:entry:{entry_id}"
                pipeline.delete(entry_key)
            pipeline.delete(index_key)
            await pipeline.execute()

            count = len(entry_ids)

            logger.info(
                "semantic_cache_flushed",
                org_id=str(org_id)[:8],
                deleted_count=count,
            )

            return count

        except Exception as e:
            logger.warning("semantic_cache_flush_error", error=str(e))
            return 0

    async def get_entry_count(self, org_id: UUID) -> int:
        """Get number of cached entries for an org."""
        if not self.is_available:
            return 0

        try:
            index_key = f"{_PREFIX}:{org_id}:index"
            return await self._redis.scard(index_key)
        except Exception:
            return 0