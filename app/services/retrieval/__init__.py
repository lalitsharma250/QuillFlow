"""
app/services/retrieval — Embedding, storage, and retrieval services.

Components:
  - embedder.py:      Generate embeddings (sentence-transformers, self-hosted)
  - vector_store.py:  Qdrant client (upsert, search, delete, org-scoped filtering)
  - sparse.py:        BM25 keyword search (complements dense retrieval)
  - reranker.py:      Cross-encoder reranking (improves precision on top-K results)

Retrieval flow:
  Query → embed → dense search (Qdrant) ─┐
                                          ├→ merge → rerank → top-K results
  Query → BM25 sparse search ────────────┘
"""
