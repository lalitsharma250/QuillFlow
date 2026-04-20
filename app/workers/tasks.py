"""
app/workers/tasks.py

Background task definitions for ARQ.

These functions run in the WORKER process, not in the API process.
They have their own DB connections and service instances.

Each task function receives an ARQ context dict as first argument.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from arq import ArqRedis

from config import get_settings
from config.logging import setup_logging
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from qdrant_client import AsyncQdrantClient
from app.services.retrieval.embedder import EmbeddingService
from app.db.repository import DocumentRepository, IngestionJobRepository


logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Worker Lifecycle
# ═══════════════════════════════════════════════════════════


async def on_worker_startup(ctx: dict) -> None:
    """
    Called once when the worker process starts.
    """
    setup_logging()
    settings = get_settings()

    logger.info("worker_starting", concurrency=settings.worker_concurrency)

    # ── Initialize DB connection pool ──────────────────
    engine = create_async_engine(
        settings.postgres_dsn,
        pool_size=settings.worker_concurrency + 2,
        max_overflow=5,
        pool_pre_ping=True,        # ← Test connection before use
        pool_recycle=300,          # ← Recycle connections every 5 min
        pool_timeout=30,           # ← Wait up to 30s for connection
    )
    ctx["db_engine"] = engine
    ctx["db_session_factory"] = async_sessionmaker(engine, expire_on_commit=False)

    # ── Initialize Qdrant client (cloud or local) ─────
    if settings.qdrant_use_cloud:
        logger.info("connecting_qdrant_cloud", url=settings.qdrant_url)
        ctx["qdrant_client"] = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value(),
            prefer_grpc=False,
            timeout=30,
        )
    else:
        logger.info("connecting_qdrant_local", host=settings.qdrant_host)
        ctx["qdrant_client"] = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            prefer_grpc=settings.qdrant_prefer_grpc,
        )

    # ── Initialize embedding service (Voyage AI) ──────
    ctx["embedder"] = EmbeddingService(model_name=settings.embedding_model_name)
    await ctx["embedder"].load()

    logger.info("worker_ready")


async def on_worker_shutdown(ctx: dict) -> None:
    """Called once when the worker process shuts down."""
    logger.info("worker_shutting_down")

    if "db_engine" in ctx:
        await ctx["db_engine"].dispose()

    if "qdrant_client" in ctx:
        await ctx["qdrant_client"].close()

    if "embedder" in ctx and hasattr(ctx["embedder"], "close"):
        await ctx["embedder"].close()

    logger.info("worker_stopped")

# ═══════════════════════════════════════════════════════════
# Task: Bulk Ingestion Job
# ═══════════════════════════════════════════════════════════


async def process_bulk_ingestion_job(ctx: dict, job_id: str) -> dict:
    """
    Process a bulk ingestion job.
    Orchestrates processing of all documents in the job.
    """
    settings = get_settings()
    job_uuid = UUID(job_id)

    logger.info("bulk_job_started", job_id=job_id)

    db_session_factory = ctx["db_session_factory"]

    # ── Load job and its documents from DB ─────────────
    async with db_session_factory() as session:
        job_repo = IngestionJobRepository(session)
        job = await job_repo.get_job_internal(job_uuid)

        if job is None:
            logger.error("bulk_job_not_found", job_id=job_id)
            return {"error": "Job not found"}

        await job_repo.update_job_status(job_uuid, "processing")
        document_ids = await job_repo.get_job_document_ids(job_uuid)
        await session.commit()

    # ── Process documents with controlled concurrency ──
    semaphore = asyncio.Semaphore(settings.worker_concurrency)
    processed = 0
    failed = 0

    async def _process_one(doc_id: UUID) -> bool:
        """Process a single document. Returns True on success."""
        async with semaphore:
            try:
                await process_single_document(ctx, document_id=str(doc_id))
                return True
            except Exception as e:
                logger.error(
                    "document_processing_failed",
                    document_id=str(doc_id),
                    error=str(e),
                )
                # Mark document as failed in DB
                async with db_session_factory() as session:
                    doc_repo = DocumentRepository(session)
                    await doc_repo.update_document_status(
                        doc_id, "failed", error_message=str(e)
                    )
                    await session.commit()
                return False

    # ── Run all documents concurrently (bounded) ───────
    tasks = [_process_one(doc_id) for doc_id in document_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if result is True:
            processed += 1
        else:
            failed += 1

    # ── Update final job progress ──────────────────────
    async with db_session_factory() as session:
        job_repo = IngestionJobRepository(session)
        await job_repo.update_job_progress(
            job_uuid,
            processed_documents=processed,
            failed_documents=failed,
        )
        await session.commit()

    # ── Finalize job ───────────────────────────────────
    if processed == 0 and failed > 0:
        final_status = "failed"
    else:
        final_status = "completed"

    async with db_session_factory() as session:
        job_repo = IngestionJobRepository(session)
        await job_repo.finalize_job(job_uuid, status=final_status)
        await session.commit()

    logger.info(
        "bulk_job_completed",
        job_id=job_id,
        processed=processed,
        failed=failed,
        status=final_status,
    )

    return {
        "job_id": job_id,
        "status": final_status,
        "processed": processed,
        "failed": failed,
    }


# ═══════════════════════════════════════════════════════════
# Task: Single Document Processing
# ═══════════════════════════════════════════════════════════


async def process_single_document(ctx: dict, document_id: str) -> dict:
    """
    Process a single document through the ingestion pipeline.

    Steps:
      1. Load document from DB
      2. Parse content (extract text if needed)
      3. Chunk text with overlap
      4. Generate embeddings for all chunks
      5. Store chunks + embeddings in Qdrant
      6. Update document status in DB
    """
    doc_uuid = UUID(document_id)
    db_session_factory = ctx["db_session_factory"]
    qdrant_client = ctx["qdrant_client"]
    embedder = ctx["embedder"]

    logger.info("document_processing_started", document_id=document_id)

    try:
        # ── 1. Load document ───────────────────────────────
        async with db_session_factory() as session:
            doc_repo = DocumentRepository(session)
            doc_record = await doc_repo.get_document_internal(doc_uuid)

            if doc_record is None:
                raise ValueError(f"Document {document_id} not found")

            await doc_repo.update_document_status(doc_uuid, "processing")
            await session.commit()

        # ── 2. Parse → Chunk → Embed → Store ──────────────
        from app.services.ingestion.pipeline import IngestionPipeline
        from app.services.retrieval.vector_store import VectorStoreService

        vector_store = VectorStoreService(client=qdrant_client)

        pipeline = IngestionPipeline(
            embedder=embedder,
            vector_store=vector_store,
        )

        result = await pipeline.process_document(
            document_id=doc_uuid,
            org_id=doc_record.org_id,
            raw_text=doc_record.raw_text,
            filename=doc_record.filename,
            content_type=doc_record.content_type,
            document_version=doc_record.version,
        )

        # ── 3. Update document status ──────────────────────
        async with db_session_factory() as session:
            doc_repo = DocumentRepository(session)
            await doc_repo.update_document_status(
                doc_uuid,
                status="indexed",
                chunk_count=result.chunk_count,
            )
            await session.commit()

        logger.info(
            "document_processing_completed",
            document_id=document_id,
            chunk_count=result.chunk_count,
        )

        return {
            "document_id": document_id,
            "status": "indexed",
            "chunk_count": result.chunk_count,
        }

    except Exception as e:
        # Convert exception to simple string for pickling
        error_message = str(e)[:500]  # Truncate long errors
        error_type = type(e).__name__

        logger.error(
            "document_processing_failed",
            document_id=document_id,
            error=error_message,
            error_type=error_type,
        )

        # Update document status to failed
        try:
            async with db_session_factory() as session:
                doc_repo = DocumentRepository(session)
                await doc_repo.update_document_status(
                    doc_uuid,
                    status="failed",
                    error_message=f"{error_type}: {error_message}",
                )
                await session.commit()
        except Exception as db_error:
            logger.error(
                "failed_to_update_document_status",
                error=str(db_error),
            )

        # Return simple dict (no exception objects)
        return {
            "document_id": document_id,
            "status": "failed",
            "error": error_message,
            "error_type": error_type,
        }