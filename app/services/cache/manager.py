"""
app/services/cache/manager.py

Unified cache manager that orchestrates L1 (exact) and L2 (semantic) caches.

This is the ONLY cache interface that graph nodes and services should use.
It encapsulates the two-tier lookup strategy:
  1. Check L1 (exact match) — O(1), ~0.1ms
  2. If miss, check L2 (semantic match) — O(n), ~1-5ms for <10K entries
  3. If miss, return None (caller proceeds with LLM generation)
  4. After generation, write to both L1 and L2

Graph nodes call: cache_manager.lookup() and cache_manager.store()
They don't need to know about exact vs semantic internals.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from redis.asyncio import Redis

from app.services.cache.exact import ExactMatchCache
from app.services.cache.semantic import SemanticCache
from config import get_settings

logger = structlog.get_logger(__name__)


class CacheManager:
    """
    Two-tier cache manager.

    Usage:
        manager = CacheManager(redis_client)

        # Lookup (checks L1, then L2)
        hit = await manager.lookup(
            query="What is RAG?",
            query_embedding=[0.1, 0.2, ...],
            org_id=uuid,
        )

        if hit:
            return hit.response  # Cached!

        # After generating response, store in both tiers
        await manager.store(
            query="What is RAG?",
            query_embedding=[0.1, 0.2, ...],
            org_id=uuid,
            response=response_dict,
            document_ids=[doc1_id, doc2_id],
        )
    """

    def __init__(self, redis: Redis | None) -> None:
        self._exact = ExactMatchCache(redis)
        self._semantic = SemanticCache(redis)
        self._settings = get_settings()

    @property
    def is_available(self) -> bool:
        return self._exact.is_available

    async def lookup(
        self,
        query: str,
        query_embedding: list[float] | None,
        org_id: UUID,
    ) -> CacheLookupResult | None:
        """
        Two-tier cache lookup.

        Args:
            query: The user's query text
            query_embedding: Query embedding (needed for L2, can be None to skip L2)
            org_id: Organization UUID

        Returns:
            CacheLookupResult if found (with cache tier info), None on miss
        """
        # ── L1: Exact match ───────────────────────────
        exact_hit = await self._exact.get(query=query, org_id=org_id)
        if exact_hit is not None:
            return CacheLookupResult(
                response=exact_hit,
                cache_tier="exact",
            )

        # ── L2: Semantic match ────────────────────────
        if query_embedding is not None:
            semantic_hit = await self._semantic.get(
                query_embedding=query_embedding,
                org_id=org_id,
            )
            if semantic_hit is not None:
                return CacheLookupResult(
                    response=semantic_hit,
                    cache_tier="semantic",
                )

        return None

    async def store(
        self,
        query: str,
        query_embedding: list[float] | None,
        org_id: UUID,
        response: dict,
        document_ids: list[UUID] | None = None,
    ) -> None:
        """
        Store a response in both cache tiers.

        Args:
            query: The user's query text
            query_embedding: Query embedding (for L2 storage)
            org_id: Organization UUID
            response: Response dict to cache
            document_ids: Source document IDs for invalidation tracking
        """
        # Store in L1 (exact)
        await self._exact.set(
            query=query,
            org_id=org_id,
            response=response,
            document_ids=document_ids,
        )

        # Store in L2 (semantic) if we have an embedding
        if query_embedding is not None:
            await self._semantic.set(
                query_embedding=query_embedding,
                org_id=org_id,
                response=response,
                document_ids=document_ids,
            )

    async def invalidate_by_document(
        self,
        document_id: UUID,
        org_id: UUID,
    ) -> dict[str, int]:
        """
        Invalidate cache entries across both tiers for a re-ingested document.

        Returns:
            Dict with invalidation counts per tier
        """
        exact_count = await self._exact.invalidate_by_document(
            document_id=document_id,
            org_id=org_id,
        )
        semantic_count = await self._semantic.invalidate_by_document(
            document_id=document_id,
            org_id=org_id,
        )

        total = exact_count + semantic_count
        if total > 0:
            logger.info(
                "cache_invalidated_by_document",
                document_id=str(document_id),
                exact_invalidated=exact_count,
                semantic_invalidated=semantic_count,
            )

        return {
            "exact": exact_count,
            "semantic": semantic_count,
            "total": total,
        }

    async def invalidate_by_org(self, org_id: UUID) -> dict[str, int]:
        """
        Flush all cache entries for an organization.

        Returns:
            Dict with deletion counts per tier
        """
        exact_count = await self._exact.invalidate_by_org(org_id)
        semantic_count = await self._semantic.invalidate_by_org(org_id)

        return {
            "exact": exact_count,
            "semantic": semantic_count,
            "total": exact_count + semantic_count,
        }

    async def get_stats(self, org_id: UUID) -> dict:
        """Get cache statistics for monitoring."""
        exact_stats = await self._exact.get_stats(org_id)
        semantic_count = await self._semantic.get_entry_count(org_id)

        return {
            "available": self.is_available,
            "exact": exact_stats,
            "semantic": {
                "entry_count": semantic_count,
            },
        }


class CacheLookupResult:
    """
    Result of a cache lookup.
    Carries the cached response plus metadata about which tier matched.
    """

    __slots__ = ("response", "cache_tier")

    def __init__(self, response: dict, cache_tier: str) -> None:
        self.response = response
        self.cache_tier = cache_tier  # "exact" or "semantic"

    @property
    def is_exact(self) -> bool:
        return self.cache_tier == "exact"

    @property
    def is_semantic(self) -> bool:
        return self.cache_tier == "semantic"