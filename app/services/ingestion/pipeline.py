"""
app/services/ingestion/pipeline.py

Single-document ingestion pipeline.

Orchestrates: parse → chunk → embed → store

This is called by:
  - The ARQ worker (for async processing)
  - Directly in tests

It does NOT manage database status updates — the caller (worker) does that.
This keeps the pipeline focused on the data transformation.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
import asyncio

from app.models.domain import Chunk
from app.services.ingestion.parser import DocumentParser, ParsedSection
from app.services.ingestion.chunker import TextChunker

logger = structlog.get_logger(__name__)


@dataclass
class IngestionResult:
    """Result of processing a single document."""

    document_id: UUID
    chunk_count: int
    chunks: list[Chunk]  # With embeddings populated


class IngestionPipeline:
    """
    Processes a single document through the full ingestion pipeline.

    Dependencies are injected — no global state.
    The pipeline is stateless and can process multiple documents.

    Usage:
        pipeline = IngestionPipeline(
            embedder=embedder_service,
            vector_store=vector_store_service,
        )
        result = await pipeline.process_document(
            document_id=uuid,
            org_id=uuid,
            raw_text="...",
            filename="paper.pdf",
            content_type="pdf",
        )
    """

    def __init__(
        self,
        embedder,       # EmbeddingService (Section 5) — generates embeddings
        vector_store,   # VectorStoreService (Section 5) — stores in Qdrant
        parser: DocumentParser | None = None,
        chunker: TextChunker | None = None,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.parser = parser or DocumentParser()
        self.chunker = chunker or TextChunker()


    async def process_document(
        self,
        document_id: UUID,
        org_id: UUID,
        raw_text: str,
        filename: str,
        content_type: str,
        document_version: int = 1,
    ) -> IngestionResult:

        logger.info(
            "ingestion_started",
            document_id=str(document_id),
            filename=filename,
            content_type=content_type,
            text_length=len(raw_text),
        )

        # ── Step 1: Parse ──────────────────────────────
        parse_result = self.parser.parse(
            raw_text=raw_text,
            content_type=content_type,
            filename=filename,
        )

        if parse_result.is_empty:
            logger.warning(
                "ingestion_empty_document",
                document_id=str(document_id),
            )
            return IngestionResult(
                document_id=document_id, chunk_count=0, chunks=[]
            )

        # ── Step 2: Chunk ──────────────────────────────
        sections_data = [
            {
                "text": section.text,
                "heading": section.heading,
                "page_number": section.page_number,
            }
            for section in parse_result.sections
        ]

        chunks = self.chunker.chunk_sections(
            sections=sections_data,
            doc_id=document_id,
            org_id=org_id,
            filename=filename,
            document_version=document_version,
        )

        if not chunks:
            return IngestionResult(
                document_id=document_id, chunk_count=0, chunks=[]
            )

        # ── Steps 3 & 4: Embed + Store ────────────────
        # For small documents: embed all, then store all
        # For large documents: embed and store in parallel streams
        embedded_chunks = await self._embed_and_store(chunks)

        logger.info(
            "ingestion_complete",
            document_id=str(document_id),
            filename=filename,
            chunk_count=len(embedded_chunks),
        )

        return IngestionResult(
            document_id=document_id,
            chunk_count=len(embedded_chunks),
            chunks=embedded_chunks,
        )

    async def _embed_and_store(
        self,
        chunks: list[Chunk],
    ) -> list[Chunk]:
        """
        Embed chunks and store in Qdrant.

        For small sets (<= batch_size): embed all → store all (simple)
        For large sets: pipeline embed batches and store in parallel
          - While batch N is being stored, batch N+1 is being embedded
          - This overlaps CPU (embedding) with I/O (Qdrant upsert)
        """

        batch_size = self.embedder.batch_size
        texts = [chunk.text for chunk in chunks]

        # Small document — simple path
        if len(chunks) <= batch_size:
            embeddings = await self.embedder.embed_batch(texts)
            embedded = self._attach_embeddings(chunks, embeddings)
            await self.vector_store.upsert_chunks(embedded)
            return embedded

        # Large document — pipelined embed + store
        all_embedded: list[Chunk] = []
        store_tasks: list[asyncio.Task] = []

        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i : i + batch_size]
            batch_texts = texts[i : i + batch_size]

            # Embed this batch
            embeddings = await self.embedder.embed_batch(batch_texts)
            embedded_batch = self._attach_embeddings(batch_chunks, embeddings)
            all_embedded.extend(embedded_batch)

            # Store this batch in background while next batch embeds
            task = asyncio.create_task(
                self.vector_store.upsert_chunks(embedded_batch)
            )
            store_tasks.append(task)

        # Wait for all store operations to complete
        if store_tasks:
            await asyncio.gather(*store_tasks)

        logger.debug(
            "pipelined_embed_store_complete",
            total_chunks=len(all_embedded),
            batches=len(store_tasks),
        )

        return all_embedded

    @staticmethod
    def _attach_embeddings(
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> list[Chunk]:
        """Create new Chunk objects with embeddings attached."""
        return [
            Chunk(
                id=chunk.id,
                text=chunk.text,
                metadata=chunk.metadata,
                embedding=embedding,
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]

    async def delete_document_chunks(
        self,
        document_id: UUID,
        org_id: UUID,
    ) -> int:
        """
        Delete all chunks for a document from Qdrant.
        Used before re-ingestion (new version) to avoid stale chunks.

        Returns:
            Number of chunks deleted
        """
        deleted = await self.vector_store.delete_by_document_id(
            document_id=document_id,
            org_id=org_id,
        )

        logger.info(
            "document_chunks_deleted",
            document_id=str(document_id),
            deleted_count=deleted,
        )

        return deleted


class IngestionError(Exception):
    """Raised when the ingestion pipeline encounters an unrecoverable error."""

    def __init__(self, document_id: UUID, step: str, message: str) -> None:
        self.document_id = document_id
        self.step = step
        self.message = message
        super().__init__(f"Ingestion failed at '{step}' for doc {document_id}: {message}")
