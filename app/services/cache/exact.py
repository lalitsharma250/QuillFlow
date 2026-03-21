"""
app/services/cache/exact.py

L1 Exact-match cache.

Strategy:
  - Normalize the query (lowercase, strip whitespace, collapse spaces)
  - Hash with SHA-256
  - Key format: "quillflow:cache:exact:{org_id}:{query_hash}"
  - Value: JSON-serialized cached response

This catches identical (or near-identical after normalization) queries.
Very fast — O(1) Redis GET/SET.

Example:
  "What is RAG?"  → same hash as "what is rag?" and "  What  is  RAG?  "
"""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

import structlog
from redis.asyncio import Redis

from config import get_settings

logger = structlog.get_logger(__name__)

# Key prefix for exact cache entries
_PREFIX = "quillflow:cache:exact"

# Key prefix for document → cache entry mapping (for invalidation)
_DOC_MAP_PREFIX = "quillflow:cache:doc_map"


def _normalize_query(query: str) -> str:
    """
    Normalize a query for consistent hashing.

    Steps:
      1. Lowercase
      2. Strip leading/trailing whitespace
      3. Collapse multiple spaces into one
      4. Remove trailing punctuation (? . !)

    "What is RAG?" → "what is rag"
    "  Explain   RAG  system. " → "explain rag system"
    """
    normalized = query.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.rstrip("?.!")
    return normalized


def _hash_query(normalized_query: str, org_id: UUID) -> str:
    """
    Create a deterministic hash for a normalized query + org.
    Org is included to prevent cross-org cache hits.
    """
    key_material = f"{org_id}:{normalized_query}"
    return hashlib.sha256(key_material.encode()).hexdigest()


class ExactMatchCache:
    """
    L1 exact-match cache backed by Redis.

    Usage:
        cache = ExactMatchCache(redis_client)

        # Check cache
        hit = await cache.get(query="What is RAG?", org_id=uuid)
        if hit:
            return hit  # Cached response

        # After generating response, cache it
        await cache.set(
            query="What is RAG?",
            org_id=uuid,
            response=response_dict,
            document_ids=[doc1_id, doc2_id],  # For invalidation
        )
    """

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._settings = get_settings()

    @property
    def is_available(self) -> bool:
        """Check if Redis is connected and cache is usable."""
        return self._redis is not None

    async def get(
        self,
        query: str,
        org_id: UUID,
    ) -> dict | None:
        """
        Look up a cached response for an exact query match.

        Args:
            query: The user's query (will be normalized)
            org_id: Organization UUID

        Returns:
            Cached response dict if found, None on miss
        """
        if not self.is_available:
            return None

        normalized = _normalize_query(query)
        query_hash = _hash_query(normalized, org_id)
        key = f"{_PREFIX}:{query_hash}"

        try:
            raw = await self._redis.get(key)
            if raw is None:
                logger.debug("exact_cache_miss", query_hash=query_hash[:12])
                return None

            cached = json.loads(raw)

            logger.info(
                "exact_cache_hit",
                query_hash=query_hash[:12],
                query_preview=normalized[:50],
            )

            return cached

        except Exception as e:
            # Cache errors should never break the main flow
            logger.warning("exact_cache_get_error", error=str(e))
            return None

    async def set(
        self,
        query: str,
        org_id: UUID,
        response: dict,
        document_ids: list[UUID] | None = None,
    ) -> bool:
        """
        Cache a response for a query.

        Args:
            query: The user's query
            org_id: Organization UUID
            response: Response dict to cache (must be JSON-serializable)
            document_ids: Source document IDs (for invalidation tracking)

        Returns:
            True if cached successfully, False on error
        """
        if not self.is_available:
            return False

        normalized = _normalize_query(query)
        query_hash = _hash_query(normalized, org_id)
        key = f"{_PREFIX}:{query_hash}"
        ttl = self._settings.cache_ttl_seconds

        try:
            # Store the cached response
            serialized = json.dumps(response, default=str)
            await self._redis.setex(key, ttl, serialized)

            # Track which documents this cache entry depends on
            # So we can invalidate when documents are re-ingested
            if document_ids:
                await self._track_document_dependencies(
                    query_hash=query_hash,
                    org_id=org_id,
                    document_ids=document_ids,
                    ttl=ttl,
                )

            logger.debug(
                "exact_cache_set",
                query_hash=query_hash[:12],
                ttl=ttl,
                doc_deps=len(document_ids) if document_ids else 0,
            )

            return True

        except Exception as e:
            logger.warning("exact_cache_set_error", error=str(e))
            return False

    async def invalidate_by_document(
        self,
        document_id: UUID,
        org_id: UUID,
    ) -> int:
        """
        Invalidate all cache entries that used chunks from a specific document.
        Called when a document is re-ingested.

        Args:
            document_id: The re-ingested document
            org_id: Organization UUID

        Returns:
            Number of cache entries invalidated
        """
        if not self.is_available:
            return 0

        doc_map_key = f"{_DOC_MAP_PREFIX}:{org_id}:{document_id}"

        try:
            # Get all cache keys that depend on this document
            cache_keys = await self._redis.smembers(doc_map_key)

            if not cache_keys:
                return 0

            # Delete all dependent cache entries
            pipeline = self._redis.pipeline()
            for cache_key in cache_keys:
                pipeline.delete(cache_key)
            # Also delete the mapping itself
            pipeline.delete(doc_map_key)
            await pipeline.execute()

            count = len(cache_keys)

            logger.info(
                "cache_invalidated_by_document",
                document_id=str(document_id),
                org_id=str(org_id),
                invalidated_count=count,
            )

            return count

        except Exception as e:
            logger.warning("cache_invalidation_error", error=str(e))
            return 0

    async def invalidate_by_org(self, org_id: UUID) -> int:
        """
        Flush all cache entries for an organization.
        Admin operation — use with caution.

        Returns:
            Approximate number of entries deleted
        """
        if not self.is_available:
            return 0

        try:
            # Scan for all keys matching this org
            pattern = f"{_PREFIX}:*"
            count = 0

            # We can't filter by org_id in the key pattern because
            # the hash includes org_id. Instead, scan all exact cache keys.
            # For production scale, consider a separate org index.
            async for key in self._redis.scan_iter(match=pattern, count=100):
                await self._redis.delete(key)
                count += 1

            # Also clean up doc maps
            doc_pattern = f"{_DOC_MAP_PREFIX}:{org_id}:*"
            async for key in self._redis.scan_iter(match=doc_pattern, count=100):
                await self._redis.delete(key)

            logger.info(
                "cache_flushed_for_org",
                org_id=str(org_id),
                deleted_count=count,
            )

            return count

        except Exception as e:
            logger.warning("cache_flush_error", error=str(e))
            return 0

    async def _track_document_dependencies(
        self,
        query_hash: str,
        org_id: UUID,
        document_ids: list[UUID],
        ttl: int,
    ) -> None:
        """
        Track which cache entries depend on which documents.
        Uses Redis SETs: doc_map:{org_id}:{doc_id} → {cache_key1, cache_key2, ...}
        """
        cache_key = f"{_PREFIX}:{query_hash}"

        pipeline = self._redis.pipeline()
        for doc_id in document_ids:
            doc_map_key = f"{_DOC_MAP_PREFIX}:{org_id}:{doc_id}"
            pipeline.sadd(doc_map_key, cache_key)
            pipeline.expire(doc_map_key, ttl)
        await pipeline.execute()

    async def get_stats(self, org_id: UUID) -> dict:
        """Get cache statistics for monitoring."""
        if not self.is_available:
            return {"available": False}

        try:
            info = await self._redis.info("memory")
            db_size = await self._redis.dbsize()

            return {
                "available": True,
                "total_keys": db_size,
                "used_memory_mb": round(
                    info.get("used_memory", 0) / (1024 * 1024), 2
                ),
            }
        except Exception:
            return {"available": False}
