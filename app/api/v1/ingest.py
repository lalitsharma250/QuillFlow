"""
app/api/v1/ingest.py

Document ingestion endpoints:
  POST /v1/ingest           — Single document (async via worker)
  POST /v1/ingest/bulk      — Bulk documents (async via worker)
  GET  /v1/ingest/jobs/{id} — Poll job progress
"""

from __future__ import annotations

import base64
from io import BytesIO
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.api.middleware.auth import get_auth_context
from app.api.middleware.rbac import require_editor, require_viewer
from app.api.middleware.rate_limit import RateLimiter
from app.db.repository import (
    AuditRepository,
    DocumentRepository,
    IngestionJobRepository,
)
from app.dependencies import get_arq_pool, get_db_session
from app.models.domain import AuthContext
from app.models.requests import BulkIngestRequest, IngestRequest
from app.models.responses import (
    BulkIngestResponse,
    IngestResponse,
    JobDocumentStatus,
    JobStatusResponse,
)
from app.services.ingestion.bulk import calculate_job_progress, create_bulk_ingestion_job

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ingestion"])


# ═══════════════════════════════════════════════════════════
# PDF / Content Pre-processing
# ═══════════════════════════════════════════════════════════

async def _create_or_version_document(
    doc_repo: DocumentRepository,
    org_id: UUID,
    filename: str,
    content_type: str,
    processed_content: str,
    metadata: dict,
    vector_store=None,
) -> tuple:
    """
    Create a new document or a new version if filename already exists.
    Returns (document, is_new_version, old_version_number).
    """
    existing = await doc_repo.find_existing_document(org_id, filename)

    if existing:
        # Create new version
        doc = await doc_repo.create_new_version(
            org_id=org_id,
            filename=filename,
            content_type=content_type,
            raw_text=processed_content,
            metadata=metadata,
            previous_version=existing.version,
        )

        # Deactivate old versions
        deactivated = await doc_repo.deactivate_old_versions(
            org_id=org_id,
            filename=filename,
            keep_version=doc.version,
        )

        # Delete old chunks from Qdrant
        if vector_store and deactivated > 0:
            try:
                await vector_store.delete_by_document_id(
                    document_id=existing.id,
                    org_id=org_id,
                )
            except Exception as e:
                logger.warning(
                    "old_version_chunk_cleanup_failed",
                    filename=filename,
                    error=str(e),
                )

        logger.info(
            "document_versioned",
            filename=filename,
            old_version=existing.version,
            new_version=doc.version,
            deactivated_versions=deactivated,
        )

        return doc, True, existing.version
    else:
        # Create new document (version 1)
        doc = await doc_repo.create_document(
            org_id=org_id,
            filename=filename,
            content_type=content_type,
            raw_text=processed_content,
            metadata=metadata,
        )
        return doc, False, 0

def extract_text_from_content(content: str, content_type: str, filename: str) -> str:
    """
    Pre-process document content before ingestion.
    Handles base64-encoded PDFs sent from the frontend.
    Text/HTML/Markdown are returned as-is.
    """
    if content_type != "pdf":
        return content

    # Try to decode as base64 (frontend sends PDFs as base64)
    try:
        pdf_bytes = base64.b64decode(content)
    except Exception:
        # Not base64 — might be already-extracted text
        if content.strip() and not content.startswith('%PDF'):
            return content
        raise HTTPException(
            status_code=400,
            detail=f"'{filename}' — could not decode PDF. Please ensure it's a valid PDF file.",
        )

    # Extract text from PDF bytes
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(pdf_bytes))

        if len(reader.pages) == 0:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' — PDF has no pages.",
            )

        text_parts = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                text_parts.append(f"--- Page {i} ---\n{text.strip()}")

        if not text_parts:
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' — no text could be extracted from PDF. It may be image-based or scanned.",
            )

        extracted = "\n\n".join(text_parts)

        logger.info(
            "pdf_text_extracted",
            filename=filename,
            pages=len(reader.pages),
            text_length=len(extracted),
        )

        return extracted

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"'{filename}' — failed to parse PDF: {str(e)}",
        )


# ═══════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════

@router.post("/ingest", response_model=IngestResponse)
async def ingest_single(
    request: IngestRequest,
    http_request: Request,
    _rate_limit: None = Depends(RateLimiter("ingest")),
    auth: AuthContext = Depends(require_editor),
    session: AsyncSession = Depends(get_db_session),
):
    arq_pool = getattr(http_request.app.state, "arq_pool", None)
    vector_store = getattr(http_request.app.state, "vector_store", None)

    processed_content = extract_text_from_content(
        content=request.content,
        content_type=request.content_type,
        filename=request.filename,
    )

    doc_repo = DocumentRepository(session)
    doc, is_new_version, old_version = await _create_or_version_document(
        doc_repo=doc_repo,
        org_id=auth.org_id,
        filename=request.filename,
        content_type=request.content_type,
        processed_content=processed_content,
        metadata=request.metadata,
        vector_store=vector_store,
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="ingest_single",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="document",
        resource_id=doc.id,
        detail={
            "filename": request.filename,
            "content_type": request.content_type,
            "is_new_version": is_new_version,
            "version": doc.version,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    if arq_pool is not None:
        await arq_pool.enqueue_job(
            "process_single_document",
            str(doc.id),
            _job_id=f"ingest-{doc.id}",
        )

    version_msg = f" (v{doc.version}, replacing v{old_version})" if is_new_version else ""
    message = f"Document accepted{version_msg}. Processing in background."

    return IngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        status="processing",
        message=message,
    )

@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    http_request: Request,
    file: UploadFile = File(..., description="Document file (PDF, TXT, HTML, MD)"),
    metadata_json: Optional[str] = Form(default=None, description="Optional JSON metadata"),
    _rate_limit: None = Depends(RateLimiter("ingest")),
    auth: AuthContext = Depends(require_editor),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Upload a document file directly via multipart form.
    
    Supports: PDF, TXT, HTML, Markdown
    Max size: 20MB
    
    This is the preferred upload method for large files.
    The JSON /ingest endpoint still works for programmatic text ingestion.
    """
    import json

    arq_pool = getattr(http_request.app.state, "arq_pool", None)

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Detect content type from extension
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    content_type_map = {
        "pdf": "pdf",
        "txt": "text",
        "text": "text",
        "html": "html",
        "htm": "html",
        "md": "markdown",
        "markdown": "markdown",
    }

    content_type = content_type_map.get(ext)
    if not content_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Supported: PDF, TXT, HTML, MD",
        )

    # Read file content
    try:
        raw_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    # Check size (20MB max)
    if len(raw_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    # Extract text based on content type
    if content_type == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(raw_bytes))
            text_parts = []
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text()
                if text and text.strip():
                    text_parts.append(f"--- Page {i} ---\n{text.strip()}")

            if not text_parts:
                raise HTTPException(
                    status_code=400,
                    detail=f"No text could be extracted from '{file.filename}'. It may be image-based.",
                )

            processed_content = "\n\n".join(text_parts)

            logger.info(
                "pdf_uploaded",
                filename=file.filename,
                pages=len(reader.pages),
                text_length=len(processed_content),
                file_size=len(raw_bytes),
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")
    else:
        # Text-based files — decode as UTF-8
        try:
            processed_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                processed_content = raw_bytes.decode("latin-1")
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to decode '{file.filename}'. Ensure it's a valid text file.",
                )

    if not processed_content.strip():
        raise HTTPException(status_code=400, detail=f"'{file.filename}' is empty.")

    # Parse metadata
    metadata = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid metadata JSON")

    # Create document record
    doc_repo = DocumentRepository(session)
    vector_store = getattr(http_request.app.state, "vector_store", None)

    doc, is_new_version, old_version = await _create_or_version_document(
        doc_repo=doc_repo,
        org_id=auth.org_id,
        filename=file.filename,
        content_type=content_type,
        processed_content=processed_content,
        metadata=metadata,
        vector_store=vector_store,
    )
    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="ingest_upload",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="document",
        resource_id=doc.id,
        detail={
            "filename": file.filename,
            "content_type": content_type,
            "file_size": len(raw_bytes),
            "is_new_version": is_new_version,
            "version": doc.version,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    # Enqueue for processing
    if arq_pool is not None:
        await arq_pool.enqueue_job(
            "process_single_document",
            str(doc.id),
            _job_id=f"ingest-{doc.id}",
        )

    version_msg = f" (v{doc.version}, replacing v{old_version})" if is_new_version else ""

    return IngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        status="processing",
        message=f"Document uploaded{version_msg}. Processing in background.",
    )


@router.post("/ingest/upload/bulk", response_model=BulkIngestResponse)
async def ingest_upload_bulk(
    http_request: Request,
    files: list[UploadFile] = File(..., description="Multiple document files"),
    _rate_limit: None = Depends(RateLimiter("ingest_bulk")),
    auth: AuthContext = Depends(require_editor),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Upload multiple document files via multipart form.
    Max 20 files per request, 20MB each.
    """
    arq_pool = getattr(http_request.app.state, "arq_pool", None)

    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 files per bulk upload")

    content_type_map = {
        "pdf": "pdf", "txt": "text", "text": "text",
        "html": "html", "htm": "html", "md": "markdown", "markdown": "markdown",
    }

    # Process all files
    documents = []
    for file in files:
        if not file.filename:
            continue

        ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
        content_type = content_type_map.get(ext)

        if not content_type:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file: '{file.filename}' (.{ext})",
            )

        raw_bytes = await file.read()

        if len(raw_bytes) > 20 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"'{file.filename}' is too large (max 20MB)",
            )

        # Extract text
        if content_type == "pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(BytesIO(raw_bytes))
                text_parts = []
                for i, page in enumerate(reader.pages, 1):
                    text = page.extract_text()
                    if text and text.strip():
                        text_parts.append(f"--- Page {i} ---\n{text.strip()}")
                processed_content = "\n\n".join(text_parts) if text_parts else ""
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse '{file.filename}': {str(e)}",
                )
        else:
            try:
                processed_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                processed_content = raw_bytes.decode("latin-1", errors="replace")

        if not processed_content.strip():
            continue

        documents.append({
            "filename": file.filename,
            "content_type": content_type,
            "content": processed_content,
        })

    if not documents:
        raise HTTPException(status_code=400, detail="No valid documents found in upload")

    # Create document records
    doc_repo = DocumentRepository(session)
    job_repo = IngestionJobRepository(session)
    doc_ids = []

    for doc_data in documents:
        doc = await doc_repo.create_document(
            org_id=auth.org_id,
            filename=doc_data["filename"],
            content_type=doc_data["content_type"],
            raw_text=doc_data["content"],
            metadata={},
        )
        doc_ids.append(doc.id)

    # Create job
    job = await job_repo.create_job(org_id=auth.org_id, document_ids=doc_ids)

    # Audit
    audit = AuditRepository(session)
    await audit.log(
        action="ingest_upload_bulk",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="ingestion_job",
        resource_id=job.id,
        detail={
            "document_count": len(documents),
            "filenames": [d["filename"] for d in documents[:10]],
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    if arq_pool is not None:
        await arq_pool.enqueue_job(
            "process_bulk_ingestion_job",
            str(job.id),
            _job_id=f"bulk-{job.id}",
        )

    return BulkIngestResponse(
        job_id=job.id,
        status="accepted",
        total_documents=len(documents),
        document_ids=doc_ids,
    )

@router.post("/ingest/bulk", response_model=BulkIngestResponse)
async def ingest_bulk(
    request: BulkIngestRequest,
    http_request: Request,
    _rate_limit: None = Depends(RateLimiter("ingest_bulk")),
    auth: AuthContext = Depends(require_editor),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Ingest multiple documents as a bulk job.
    Supports text, HTML, Markdown, and PDF (base64-encoded).
    """
    arq_pool = getattr(http_request.app.state, "arq_pool", None)

    # Pre-process all documents (extract text from PDFs)
    for doc in request.documents:
        doc.content = extract_text_from_content(
            content=doc.content,
            content_type=doc.content_type,
            filename=doc.filename,
        )

    # Create job + document records
    job = await create_bulk_ingestion_job(
        request=request,
        org_id=auth.org_id,
        session=session,
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="ingest_bulk",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="ingestion_job",
        resource_id=job.id,
        detail={
            "document_count": len(request.documents),
            "filenames": [d.filename for d in request.documents[:10]],
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    # Enqueue the bulk job
    if arq_pool is not None:
        await arq_pool.enqueue_job(
            "process_bulk_ingestion_job",
            str(job.id),
            _job_id=f"bulk-{job.id}",
        )

    return BulkIngestResponse(
        job_id=job.id,
        status="accepted",
        total_documents=job.total_documents,
        document_ids=job.document_ids,
    )


@router.get("/ingest/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: UUID,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """Poll the status of a bulk ingestion job."""
    job_repo = IngestionJobRepository(session)
    job, doc_statuses = await job_repo.get_job_with_document_statuses(
        job_id=job_id,
        org_id=auth.org_id,
    )

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    progress = calculate_job_progress(
        total=job.total_documents,
        processed=job.processed_documents,
        failed=job.failed_documents,
    )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        total_documents=job.total_documents,
        processed_documents=job.processed_documents,
        failed_documents=job.failed_documents,
        progress_percent=progress,
        documents=[
            JobDocumentStatus(
                document_id=ds["document_id"],
                filename=ds["filename"],
                status=ds["status"],
                error_message=ds.get("error_message"),
                chunk_count=ds.get("chunk_count"),
            )
            for ds in doc_statuses
        ],
        created_at=job.created_at,
        completed_at=job.completed_at,
    )