"""
tests/unit/test_cache.py

Tests for the caching layer.
Uses a real Redis instance (from docker-compose) or fakeredis for unit tests.
"""

import pytest
import json
from uuid import uuid4

from app.services.cache.exact import ExactMatchCache, _normalize_query, _hash_query
from app.services.cache.semantic import (
    SemanticCache,
    _cosine_similarity,
    _embedding_to_bytes,
    _bytes_to_embedding,
)
from app.services.cache.manager import CacheManager, CacheLookupResult


# ═══════════════════════════════════════════════════════════
# Query Normalization Tests
# ═══════════════════════════════════════════════════════════


class TestQueryNormalization:
    def test_lowercase(self):
        assert _normalize_query("What Is RAG?") == "what is rag"

    def test_strip_whitespace(self):
        assert _normalize_query("  hello world  ") == "hello world"

    def test_collapse_spaces(self):
        assert _normalize_query("hello   world") == "hello world"

    def test_remove_trailing_punctuation(self):
        assert _normalize_query("What is RAG?") == "what is rag"
        assert _normalize_query("Explain RAG.") == "explain rag"
        assert _normalize_query("Tell me about RAG!") == "tell me about rag"

    def test_combined_normalization(self):
        assert _normalize_query("  What  IS  rag? ") == "what is rag"

    def test_empty_string(self):
        assert _normalize_query("") == ""

    def test_same_queries_same_hash(self):
        org_id = uuid4()
        h1 = _hash_query(_normalize_query("What is RAG?"), org_id)
        h2 = _hash_query(_normalize_query("what is rag"), org_id)
        h3 = _hash_query(_normalize_query("  What  is  RAG?  "), org_id)
        assert h1 == h2 == h3

    def test_different_orgs_different_hash(self):
        org1 = uuid4()
        org2 = uuid4()
        h1 = _hash_query("same query", org1)
        h2 = _hash_query("same query", org2)
        assert h1 != h2


# ═══════════════════════════════════════════════════════════
# Embedding Serialization Tests
# ═══════════════════════════════════════════════════════════


class TestEmbeddingSerialization:
    def test_roundtrip(self):
        original = [0.1, 0.2, 0.3, -0.5, 0.99]
        packed = _embedding_to_bytes(original)
        unpacked = _bytes_to_embedding(packed)

        assert len(unpacked) == len(original)
        for a, b in zip(original, unpacked):
            assert abs(a - b) < 1e-6

    def test_compact_size(self):
        # 1024-dim embedding should be 4096 bytes (4 bytes per float32)
        embedding = [0.1] * 1024
        packed = _embedding_to_bytes(embedding)
        assert len(packed) == 4096

    def test_empty_embedding(self):
        packed = _embedding_to_bytes([])
        unpacked = _bytes_to_embedding(packed)
        assert unpacked == []


# ═══════════════════════════════════════════════════════════
# Cosine Similarity Tests
# ═══════════════════════════════════════════════════════════


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [0.1, 0.2, 0.3, 0.4]
        assert abs(_cosine_similarity(v, v) - 1.0) < 0.01

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 0.01

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) < -0.99

    def test_similar_vectors(self):
        a = [0.9, 0.1, 0.05]
        b = [0.85, 0.15, 0.08]
        sim = _cosine_similarity(a, b)
        assert sim > 0.95  # Very similar


# ═══════════════════════════════════════════════════════════
# ExactMatchCache Tests (with None redis — graceful degradation)
# ═══════════════════════════════════════════════════════════


class TestExactMatchCacheNoRedis:
    """Test that cache gracefully handles missing Redis."""

    def test_not_available(self):
        cache = ExactMatchCache(redis=None)
        assert cache.is_available is False

    async def test_get_returns_none(self):
        cache = ExactMatchCache(redis=None)
        result = await cache.get("query", uuid4())
        assert result is None

    async def test_set_returns_false(self):
        cache = ExactMatchCache(redis=None)
        result = await cache.set("query", uuid4(), {"data": "test"})
        assert result is False

    async def test_invalidate_returns_zero(self):
        cache = ExactMatchCache(redis=None)
        result = await cache.invalidate_by_document(uuid4(), uuid4())
        assert result == 0


# ═══════════════════════════════════════════════════════════
# SemanticCache Tests (with None redis — graceful degradation)
# ═══════════════════════════════════════════════════════════


class TestSemanticCacheNoRedis:
    def test_not_available(self):
        cache = SemanticCache(redis=None)
        assert cache.is_available is False

    async def test_get_returns_none(self):
        cache = SemanticCache(redis=None)
        result = await cache.get([0.1, 0.2], uuid4())
        assert result is None

    async def test_set_returns_false(self):
        cache = SemanticCache(redis=None)
        result = await cache.set([0.1, 0.2], uuid4(), {"data": "test"})
        assert result is False


# ═══════════════════════════════════════════════════════════
# CacheManager Tests
# ═══════════════════════════════════════════════════════════


class TestCacheManagerNoRedis:
    """Test CacheManager with no Redis — should degrade gracefully."""

    async def test_lookup_returns_none(self):
        manager = CacheManager(redis=None)
        result = await manager.lookup("query", [0.1, 0.2], uuid4())
        assert result is None

    async def test_store_doesnt_crash(self):
        manager = CacheManager(redis=None)
        # Should not raise
        await manager.store(
            query="test",
            query_embedding=[0.1, 0.2],
            org_id=uuid4(),
            response={"content": "test"},
        )

    async def test_invalidate_returns_zeros(self):
        manager = CacheManager(redis=None)
        result = await manager.invalidate_by_document(uuid4(), uuid4())
        assert result["total"] == 0

    async def test_stats_shows_unavailable(self):
        manager = CacheManager(redis=None)
        stats = await manager.get_stats(uuid4())
        assert stats["available"] is False


class TestCacheLookupResult:
    def test_exact_tier(self):
        result = CacheLookupResult(response={"data": "test"}, cache_tier="exact")
        assert result.is_exact is True
        assert result.is_semantic is False

    def test_semantic_tier(self):
        result = CacheLookupResult(response={"data": "test"}, cache_tier="semantic")
        assert result.is_exact is False
        assert result.is_semantic is True


# ═══════════════════════════════════════════════════════════
# Integration Tests (require Redis)
# ═══════════════════════════════════════════════════════════


@pytest.fixture
async def redis_client():
    """
    Create a Redis client for testing.
    Uses database 15 to avoid conflicts with dev data.
    Falls back to skip if Redis is unavailable.
    """
    from redis.asyncio import Redis

    try:
        client = Redis(host="localhost", port=6379, db=15)
        await client.ping()
        await client.flushdb()  # Clean slate
        yield client
        await client.flushdb()
        await client.aclose()
    except Exception:
        pytest.skip("Redis not available for integration tests")


class TestExactMatchCacheIntegration:
    async def test_set_and_get(self, redis_client):
        cache = ExactMatchCache(redis_client)
        org_id = uuid4()

        response = {"content": "RAG is retrieval augmented generation", "score": 0.9}

        # Set
        success = await cache.set(
            query="What is RAG?",
            org_id=org_id,
            response=response,
        )
        assert success is True

        # Get (exact same query)
        hit = await cache.get("What is RAG?", org_id)
        assert hit is not None
        assert hit["content"] == "RAG is retrieval augmented generation"

    async def test_normalization_matches(self, redis_client):
        cache = ExactMatchCache(redis_client)
        org_id = uuid4()

        await cache.set("What is RAG?", org_id, {"answer": "test"})

        # These should all match due to normalization
        assert await cache.get("what is rag", org_id) is not None
        assert await cache.get("  What  is  RAG?  ", org_id) is not None
        assert await cache.get("WHAT IS RAG", org_id) is not None

    async def test_org_isolation(self, redis_client):
        cache = ExactMatchCache(redis_client)
        org1 = uuid4()
        org2 = uuid4()

        await cache.set("What is RAG?", org1, {"answer": "org1 answer"})

        # Same query, different org — should miss
        assert await cache.get("What is RAG?", org2) is None

        # Original org — should hit
        assert await cache.get("What is RAG?", org1) is not None

    async def test_invalidation_by_document(self, redis_client):
        cache = ExactMatchCache(redis_client)
        org_id = uuid4()
        doc_id = uuid4()

        # Cache a response that depends on a document
        await cache.set(
            "What is RAG?",
            org_id,
            {"answer": "test"},
            document_ids=[doc_id],
        )

        # Verify it's cached
        assert await cache.get("What is RAG?", org_id) is not None

        # Invalidate by document
        count = await cache.invalidate_by_document(doc_id, org_id)
        assert count >= 1

        # Should be gone now
        assert await cache.get("What is RAG?", org_id) is None


class TestSemanticCacheIntegration:
    async def test_set_and_get_similar(self, redis_client):
        cache = SemanticCache(redis_client)
        org_id = uuid4()

        # Store with an embedding
        embedding = [0.1] * 128  # Simplified embedding
        await cache.set(
            query_embedding=embedding,
            org_id=org_id,
            response={"answer": "cached response"},
        )

        # Query with identical embedding — should hit
        hit = await cache.get(
            query_embedding=embedding,
            org_id=org_id,
        )
        assert hit is not None
        assert hit["answer"] == "cached response"

    async def test_dissimilar_embedding_misses(self, redis_client):
        cache = SemanticCache(redis_client)
        org_id = uuid4()

        # Store with one embedding
        embedding_a = [1.0, 0.0, 0.0] * 42 + [1.0, 0.0]  # 128 dims
        await cache.set(
            query_embedding=embedding_a,
            org_id=org_id,
            response={"answer": "cached"},
        )

        # Query with very different embedding — should miss
        embedding_b = [0.0, 1.0, 0.0] * 42 + [0.0, 1.0]  # Orthogonal
        hit = await cache.get(
            query_embedding=embedding_b,
            org_id=org_id,
        )
        assert hit is None

    async def test_org_isolation(self, redis_client):
        cache = SemanticCache(redis_client)
        org1 = uuid4()
        org2 = uuid4()
        embedding = [0.5] * 128

        await cache.set(embedding, org1, {"answer": "org1"})

        # Different org — should miss
        assert await cache.get(embedding, org2) is None

        # Same org — should hit
        assert await cache.get(embedding, org1) is not None

    async def test_invalidation_by_document(self, redis_client):
        cache = SemanticCache(redis_client)
        org_id = uuid4()
        doc_id = uuid4()
        embedding = [0.3] * 128

        await cache.set(
            query_embedding=embedding,
            org_id=org_id,
            response={"answer": "will be invalidated"},
            document_ids=[doc_id],
        )

        # Verify cached
        assert await cache.get(embedding, org_id) is not None

        # Invalidate
        count = await cache.invalidate_by_document(doc_id, org_id)
        assert count >= 1

        # Should be gone
        assert await cache.get(embedding, org_id) is None

    async def test_entry_count(self, redis_client):
        cache = SemanticCache(redis_client)
        org_id = uuid4()

        assert await cache.get_entry_count(org_id) == 0

        for i in range(5):
            embedding = [float(i) * 0.1] * 128
            await cache.set(embedding, org_id, {"answer": f"resp_{i}"})

        assert await cache.get_entry_count(org_id) == 5


class TestCacheManagerIntegration:
    async def test_l1_hit(self, redis_client):
        manager = CacheManager(redis_client)
        org_id = uuid4()

        await manager.store(
            query="What is RAG?",
            query_embedding=[0.1] * 128,
            org_id=org_id,
            response={"content": "RAG explanation"},
        )

        # Lookup — should hit L1 (exact)
        result = await manager.lookup(
            query="What is RAG?",
            query_embedding=[0.1] * 128,
            org_id=org_id,
        )
        assert result is not None
        assert result.is_exact is True
        assert result.response["content"] == "RAG explanation"

    async def test_l2_hit_when_l1_misses(self, redis_client):
        manager = CacheManager(redis_client)
        org_id = uuid4()
        embedding = [0.5] * 128

        # Store with one query text
        await manager.store(
            query="What is RAG?",
            query_embedding=embedding,
            org_id=org_id,
            response={"content": "RAG explanation"},
        )

        # Lookup with DIFFERENT query text but SAME embedding
        # L1 will miss (different text), L2 should hit (same embedding)
        result = await manager.lookup(
            query="Explain RAG to me",  # Different text
            query_embedding=embedding,  # Same embedding
            org_id=org_id,
        )
        assert result is not None
        assert result.is_semantic is True
        assert result.response["content"] == "RAG explanation"

    async def test_full_miss(self, redis_client):
        manager = CacheManager(redis_client)
        org_id = uuid4()

        result = await manager.lookup(
            query="Never seen this before",
            query_embedding=[0.9] * 128,
            org_id=org_id,
        )
        assert result is None

    async def test_invalidation_clears_both_tiers(self, redis_client):
        manager = CacheManager(redis_client)
        org_id = uuid4()
        doc_id = uuid4()

        await manager.store(
            query="What is RAG?",
            query_embedding=[0.1] * 128,
            org_id=org_id,
            response={"content": "answer"},
            document_ids=[doc_id],
        )

        # Verify both tiers have entries
        l1_hit = await manager.lookup("What is RAG?", None, org_id)
        assert l1_hit is not None

        # Invalidate
        counts = await manager.invalidate_by_document(doc_id, org_id)
        assert counts["total"] >= 1

        # Both tiers should be empty
        result = await manager.lookup("What is RAG?", [0.1] * 128, org_id)
        assert result is None

    async def test_stats(self, redis_client):
        manager = CacheManager(redis_client)
        org_id = uuid4()

        stats = await manager.get_stats(org_id)
        assert stats["available"] is True
        assert "exact" in stats
        assert "semantic" in stats