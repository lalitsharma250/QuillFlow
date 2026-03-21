"""
app/services/ingestion/bulk.py

Helpers for bulk ingestion orchestration.

The actual bulk job processing lives in app/workers/tasks.py.
This module provides shared utilities used by both the API layer
(for creating jobs) and the worker (for processing them).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import DocumentRepository, IngestionJobRepository
from app.models.domain import IngestionJob
from app.models.requests import BulkIngestRequest

logger = structlog.get_logger(__name__)


async def create_bulk_ingestion_job(
    request: BulkIngestRequest,
    org_id: UUID,
    session: AsyncSession,
) -> IngestionJob:
    """
    Create a bulk ingestion job with all its documents.

    Called by the API endpoint. Creates:
      1. DocumentRecord for each document in the request
      2. IngestionJobRecord linking them all
      3. JobDocumentLink entries

    The actual processing is done later by the ARQ worker.

    Args:
        request: Validated bulk ingest request
        org_id: Organization UUID from auth context
        session: Database session (caller manages commit)

    Returns:
        IngestionJob domain model with job ID and document IDs
    """
    doc_repo = DocumentRepository(session)
    job_repo = IngestionJobRepository(session)

    # ── Create document records ────────────────────────
    doc_data_list = [
        {
            "filename": doc.filename,
            "content_type": doc.content_type,
            "raw_text": doc.content,
            "metadata": doc.metadata,
        }
        for doc in request.documents
    ]

    documents = await doc_repo.create_documents_batch(
        org_id=org_id,
        documents=doc_data_list,
    )

    document_ids = [doc.id for doc in documents]

    # ── Create job record ──────────────────────────────
    job = await job_repo.create_job(
        org_id=org_id,
        document_ids=document_ids,
    )

    logger.info(
        "bulk_job_created",
        job_id=str(job.id),
        org_id=str(org_id),
        document_count=len(document_ids),
    )

    return job


def calculate_job_progress(
    total: int,
    processed: int,
    failed: int,
) -> float:
    """Calculate job progress as a percentage."""
    if total == 0:
        return 0.0
    done = processed + failed
    return round((done / total) * 100, 1)


def determine_job_final_status(
    total: int,
    processed: int,
    failed: int,
) -> str:
    """
    Determine the final status of a completed job.

    Rules:
      - All succeeded → "completed"
      - Some failed, some succeeded → "completed" (partial success)
      - All failed → "failed"
    """
    if processed == 0 and failed > 0:
        return "failed"
    return "completed"