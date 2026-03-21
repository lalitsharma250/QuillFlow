"""
app/services/retrieval/vector_store.py

Qdrant vector store client.

Handles:
  - Collection creation and configuration
  - Upserting chunks with embeddings and metadata
  - Dense (vector) search with org-scoped filtering
  - Deletion by document ID (for re-ingestion)
  - Health checks

All operations are org-scoped: every chunk carries org_id in its payload,
and every search filters by org_id. This ensures complete data isolation
between organizations.
"""

from __future__ import annotations

from uuid import UUID

import structlog
import asyncio
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import models

from app.models.domain import Chunk, ChunkMetadata, RetrievedChunk, RetrievalMethod
from config import get_settings
from config.constants import QDRANT_DENSE_VECTOR_NAME

logger = structlog.get_logger(__name__)


class VectorStoreService:
    """
    Qdrant vector store operations.

    Usage:
        service = VectorStoreService(client=qdrant_client)
        await service.ensure_collection()

        # Upsert chunks (ingestion)
        await service.upsert_chunks(chunks)

        # Search (retrieval)
        results = await service.search(
            query_embedding=[0.1, 0.2, ...],
            org_id=uuid,
            top_k=10,
        )
    """

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client
        self._settings = get_settings()
        self._collection_name = self._settings.qdrant_collection_name
        self._dimensions = self._settings.embedding_dimensions

    # ═══════════════════════════════════════════════════
    # Collection Management
    # ═══════════════════════════════════════════════════

    async def ensure_collection(self) -> None:
        """
        Create the collection if it doesn't exist.
        Idempotent — safe to call on every startup.

        Configures:
          - Dense vector with cosine distance
          - HNSW index for fast approximate search
          - Payload indexes for org_id and source_doc_id (filtering)
        """
        collections = await self._client.get_collections()
        existing_names = [c.name for c in collections.collections]

        if self._collection_name in existing_names:
            logger.debug(
                "collection_already_exists",
                collection=self._collection_name,
            )
            return

        logger.info(
            "creating_collection",
            collection=self._collection_name,
            dimensions=self._dimensions,
        )

        await self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                QDRANT_DENSE_VECTOR_NAME: models.VectorParams(
                    size=self._dimensions,
                    distance=models.Distance.COSINE,
                    on_disk=False,  # Keep in memory for speed
                ),
            },
            # HNSW index config for search quality/speed tradeoff
            hnsw_config=models.HnswConfigDiff(
                m=16,                    # Number of edges per node
                ef_construct=100,        # Build-time search width
                full_scan_threshold=10000,
            ),
            # Optimizers
            optimizers_config=models.OptimizersConfigDiff(
                indexing_threshold=20000,  # Start indexing after 20K points
            ),
        )

        # Create payload indexes for fast filtering
        await self._client.create_payload_index(
            collection_name=self._collection_name,
            field_name="org_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        await self._client.create_payload_index(
            collection_name=self._collection_name,
            field_name="source_doc_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        await self._client.create_payload_index(
            collection_name=self._collection_name,
            field_name="source_filename",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        logger.info(
            "collection_created",
            collection=self._collection_name,
        )

    # ═══════════════════════════════════════════════════
    # Upsert (Ingestion)
    # ═══════════════════════════════════════════════════

    async def upsert_chunks(
        self,
        chunks: list[Chunk],
        collection_name: str | None = None,
    ) -> int:
        """
        Upsert chunks with embeddings into Qdrant.
        Uses parallel batch upserts for throughput.
        """
        if not chunks:
            return 0

        collection = collection_name or self._collection_name

        missing = [c.id for c in chunks if not c.has_embedding]
        if missing:
            raise ValueError(
                f"{len(missing)} chunks are missing embeddings."
            )

        points = [self._chunk_to_point(chunk) for chunk in chunks]

        # Split into batches
        batch_size = 100
        batches = [
            points[i : i + batch_size]
            for i in range(0, len(points), batch_size)
        ]

        if len(batches) <= 1:
            # Single batch — no parallelism needed
            if batches:
                await self._client.upsert(
                    collection_name=collection,
                    points=batches[0],
                    wait=True,
                )
            return len(points)

        # Multiple batches — upsert in parallel
        # Limit concurrency to avoid overwhelming Qdrant
        semaphore = asyncio.Semaphore(4)

        async def _upsert_batch(batch: list[models.PointStruct]) -> int:
            async with semaphore:
                await self._client.upsert(
                    collection_name=collection,
                    points=batch,
                    wait=True,
                )
                return len(batch)

        results = await asyncio.gather(
            *[_upsert_batch(batch) for batch in batches]
        )

        total_upserted = sum(results)

        logger.info(
            "upsert_complete",
            collection=collection,
            chunk_count=total_upserted,
            batch_count=len(batches),
        )

        return total_upserted

    def _chunk_to_point(self, chunk: Chunk) -> models.PointStruct:
        """Convert a Chunk domain model to a Qdrant PointStruct."""
        payload = {
            # Filterable fields (indexed)
            "org_id": str(chunk.metadata.org_id),
            "source_doc_id": str(chunk.metadata.source_doc_id),
            "source_filename": chunk.metadata.source_filename,
            # Non-indexed metadata
            "text": chunk.text,
            "page_number": chunk.metadata.page_number,
            "section_heading": chunk.metadata.section_heading,
            "chunk_index": chunk.metadata.chunk_index,
            "total_chunks": chunk.metadata.total_chunks,
            "document_version": chunk.metadata.document_version,
            "created_at": chunk.metadata.created_at.isoformat(),
        }

        return models.PointStruct(
            id=str(chunk.id),  # Qdrant accepts string UUIDs
            vector={QDRANT_DENSE_VECTOR_NAME: chunk.embedding},
            payload=payload,
        )

    # ═══════════════════════════════════════════════════
    # Search (Retrieval)
    # ═══════════════════════════════════════════════════

    async def search(
    self,
    query_embedding: list[float],
    org_id: UUID,
    top_k: int | None = None,
    score_threshold: float | None = None,
    document_ids: list[UUID] | None = None,
    ) -> list[RetrievedChunk]:
        """
        Dense vector search with org-scoped filtering.
        """
        settings = self._settings
        top_k = top_k or settings.retrieval_top_k

        # ── Build filter ───────────────────────────────
        must_conditions = [
            models.FieldCondition(
                key="org_id",
                match=models.MatchValue(value=str(org_id)),
            ),
        ]

        if document_ids:
            must_conditions.append(
                models.FieldCondition(
                    key="source_doc_id",
                    match=models.MatchAny(
                        any=[str(did) for did in document_ids]
                    ),
                ),
            )

        query_filter = models.Filter(must=must_conditions)

        # ── Execute search ─────────────────────────────
        results = await self._client.query_points(
            collection_name=self._collection_name,
            query=query_embedding,
            using=QDRANT_DENSE_VECTOR_NAME,
            query_filter=query_filter,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        # query_points returns a QueryResponse with .points attribute
        points = results.points if hasattr(results, 'points') else results

        # ── Convert to domain models ──────────────────
        retrieved_chunks = [
            self._point_to_retrieved_chunk(point)
            for point in points
        ]

        logger.debug(
            "vector_search_complete",
            org_id=str(org_id),
            top_k=top_k,
            results_count=len(retrieved_chunks),
            top_score=retrieved_chunks[0].score if retrieved_chunks else None,
        )

        return retrieved_chunks

    def _point_to_retrieved_chunk(
        self, point: models.ScoredPoint
    ) -> RetrievedChunk:
        """Convert a Qdrant ScoredPoint back to a RetrievedChunk domain model."""
        payload = point.payload or {}

        metadata = ChunkMetadata(
            org_id=UUID(payload.get("org_id", "")),
            source_doc_id=UUID(payload.get("source_doc_id", "")),
            source_filename=payload.get("source_filename", ""),
            page_number=payload.get("page_number"),
            section_heading=payload.get("section_heading"),
            chunk_index=payload.get("chunk_index", 0),
            total_chunks=payload.get("total_chunks"),
            document_version=payload.get("document_version", 1),
        )

        chunk = Chunk(
            id=UUID(str(point.id)),
            text=payload.get("text", ""),
            metadata=metadata,
            embedding=None,  # Not returned from search
        )

        return RetrievedChunk(
            chunk=chunk,
            score=point.score,
            retrieval_method=RetrievalMethod.DENSE,
        )

    # ═══════════════════════════════════════════════════
    # Delete (Re-ingestion / Cleanup)
    # ═══════════════════════════════════════════════════

    async def delete_by_document_id(
        self,
        document_id: UUID,
        org_id: UUID,
    ) -> int:
        """
        Delete all chunks belonging to a specific document.
        Used before re-ingestion to avoid stale chunks.

        Both document_id AND org_id are required (defense in depth).

        Returns:
            Approximate number of points deleted
        """
        # Get count before deletion
        count_before = await self._count_by_document(document_id, org_id)

        delete_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="org_id",
                    match=models.MatchValue(value=str(org_id)),
                ),
                models.FieldCondition(
                    key="source_doc_id",
                    match=models.MatchValue(value=str(document_id)),
                ),
            ]
        )

        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(filter=delete_filter),
            wait=True,
        )

        logger.info(
            "chunks_deleted",
            document_id=str(document_id),
            org_id=str(org_id),
            deleted_count=count_before,
        )

        return count_before

    async def delete_by_org_id(self, org_id: UUID) -> int:
        """
        Delete ALL chunks for an organization.
        Use with extreme caution — this is for org deletion/cleanup only.

        Returns:
            Approximate number of points deleted
        """
        count_before = await self._count_by_org(org_id)

        delete_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="org_id",
                    match=models.MatchValue(value=str(org_id)),
                ),
            ]
        )

        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(filter=delete_filter),
            wait=True,
        )

        logger.warning(
            "org_chunks_deleted",
            org_id=str(org_id),
            deleted_count=count_before,
        )

        return count_before

    # ═══════════════════════════════════════════════════
    # Counting / Stats
    # ═══════════════════════════════════════════════════

    async def _count_by_document(self, document_id: UUID, org_id: UUID) -> int:
        """Count chunks for a specific document."""
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="org_id",
                        match=models.MatchValue(value=str(org_id)),
                    ),
                    models.FieldCondition(
                        key="source_doc_id",
                        match=models.MatchValue(value=str(document_id)),
                    ),
                ]
            ),
            exact=True,
        )
        return result.count

    async def _count_by_org(self, org_id: UUID) -> int:
        """Count all chunks for an organization."""
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="org_id",
                        match=models.MatchValue(value=str(org_id)),
                    ),
                ]
            ),
            exact=True,
        )
        return result.count

    async def get_collection_info(self) -> dict:
        """Get collection statistics for health checks and monitoring."""
        try:
            info = await self._client.get_collection(self._collection_name)
            return {
                "collection": self._collection_name,
                "points_count": info.points_count,
                "status": info.status.value if hasattr(info.status, 'value') else str(info.status),
            }
        except Exception as e:
            return {
                "collection": self._collection_name,
                "status": "error",
                "error": str(e),
            }

    # ═══════════════════════════════════════════════════
    # Health Check
    # ═══════════════════════════════════════════════════

    async def health_check(self) -> tuple[bool, float]:
        """
        Check Qdrant connectivity and measure latency.

        Returns:
            (is_healthy, latency_ms)
        """
        import time

        start = time.monotonic()
        try:
            await self._client.get_collections()
            latency = (time.monotonic() - start) * 1000
            return True, round(latency, 2)
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.error("qdrant_health_check_failed", error=str(e))
            return False, round(latency, 2)


# ═══════════════════════════════════════════════════════════
# FastAPI Lifespan Hooks
# ═══════════════════════════════════════════════════════════


async def init_qdrant(app: FastAPI) -> None:
    """
    Initialize Qdrant client and ensure collection exists.
    Called during FastAPI lifespan startup.
    """
    settings = get_settings()

    client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=settings.qdrant_prefer_grpc,
    )

    service = VectorStoreService(client=client)
    await service.ensure_collection()

    app.state.qdrant_client = client
    app.state.vector_store = service


async def close_qdrant(app: FastAPI) -> None:
    """Close Qdrant client connection."""
    client: AsyncQdrantClient | None = getattr(app.state, "qdrant_client", None)
    if client is not None:
        await client.close()