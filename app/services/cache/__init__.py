"""
app/services/cache — Two-tier caching for QuillFlow.

Cache layers:
  L1 (Exact):    Hash of normalized query → cached response
                 Fast O(1) Redis lookup. Catches identical queries.

  L2 (Semantic): Query embedding cosine similarity → cached response
                 Catches paraphrased queries ("What is RAG?" ≈ "Explain RAG")
                 Uses a small Redis-backed vector index.

Cache scoping:
  All cache entries are scoped by org_id.
  Org A's cached responses are never returned to Org B.

Cache invalidation:
  - TTL-based: entries expire after configurable duration (default 24h)
  - Document-based: when a document is re-ingested, all cache entries
    that used chunks from that document are invalidated
  - Manual: admin can flush cache for an org

Components:
  - connection.py: Redis client initialization
  - exact.py:      L1 exact-match cache
  - semantic.py:   L2 semantic similarity cache
"""
