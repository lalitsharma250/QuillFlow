"""
tests/integration/test_cache_integration.py

Integration tests for the full two-tier cache flow.
Requires Redis (from docker-compose).
"""

import pytest
from uuid import uuid4

from app.services.cache.manager import CacheManager


@pytest.fixture
async def cache_manager(redis_client):
    """Create a CacheManager with real Redis."""
    return CacheManager(redis=redis_client)


class TestCacheManagerFlow:
    """Test the full L1 + L2 cache flow."""

    async def test_store_and_lookup_exact(self, cache_manager):
        org_id = uuid4()
        embedding = [0.5] * 128

        # Store
        await cache_manager.store(
            query="What is RAG?",
            query_embedding=embedding,
            org_id=org_id,
            response={"content": "RAG is retrieval augmented generation"},
            document_ids=[uuid4()],
        )

        # Lookup exact (L1)
        result = await cache_manager.lookup(
            query="What is RAG?",
            query_embedding=embedding,
            org_id=org_id,
        )

        assert result is not None
        assert result.is_exact
        assert result.response["content"] == "RAG is retrieval augmented generation"

    async def test_semantic_fallback(self, cache_manager):
        org_id = uuid4()
        embedding = [0.5] * 128

        # Store with one query
        await cache_manager.store(
            query="What is RAG?",
            query_embedding=embedding,
            org_id=org_id,
            response={"content": "RAG explanation"},
        )

        # Lookup with different text but same embedding (L1 miss, L2 hit)
        result = await cache_manager.lookup(
            query="Explain RAG to me please",
            query_embedding=embedding,
            org_id=org_id,
        )

        assert result is not None
        assert result.is_semantic

    async def test_invalidation_clears_both_tiers(self, cache_manager):
        org_id = uuid4()
        doc_id = uuid4()
        embedding = [0.3] * 128

        await cache_manager.store(
            query="Test query",
            query_embedding=embedding,
            org_id=org_id,
            response={"content": "answer"},
            document_ids=[doc_id],
        )

        # Verify cached
        assert await cache_manager.lookup("Test query", embedding, org_id) is not None

        # Invalidate
        counts = await cache_manager.invalidate_by_document(doc_id, org_id)
        assert counts["total"] >= 1

        # Verify cleared
        assert await cache_manager.lookup("Test query", embedding, org_id) is None

    async def test_org_isolation(self, cache_manager):
        org1 = uuid4()
        org2 = uuid4()
        embedding = [0.7] * 128

        await cache_manager.store(
            query="Shared query",
            query_embedding=embedding,
            org_id=org1,
            response={"content": "org1 answer"},
        )

        # Different org should miss
        assert await cache_manager.lookup("Shared query", embedding, org2) is None

        # Same org should hit
        assert await cache_manager.lookup("Shared query", embedding, org1) is not None