"""
app/api/v1/documents.py

Document management endpoints:
  GET /v1/documents         — List documents (paginated, filterable)
  GET /v1/documents/{id}    — Get single document status
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.rbac import require_viewer
from app.db.repository import DocumentRepository
from app.dependencies import get_db_session
from app.models.domain import AuthContext
from app.models.responses import DocumentListResponse, DocumentResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    status: str | None = Query(default=None, description="Filter by status"),
):
    """
    List documents for the authenticated user's organization.

    Supports pagination and optional status filtering.
    """
    doc_repo = DocumentRepository(session)
    documents, total = await doc_repo.list_documents(
        org_id=auth.org_id,
        page=page,
        page_size=page_size,
        status=status,
    )

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                document_id=doc.id,
                filename=doc.filename,
                content_type=doc.content_type,
                status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
                error_message=doc.error_message,
                chunk_count=doc.chunk_count,
                version=doc.version,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
            for doc in documents
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Get the status and details of a single document.
    """
    doc_repo = DocumentRepository(session)
    doc = await doc_repo.get_document(
        document_id=document_id,
        org_id=auth.org_id,
    )

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentResponse(
        document_id=doc.id,
        filename=doc.filename,
        content_type=doc.content_type,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        error_message=doc.error_message,
        chunk_count=doc.chunk_count,
        version=doc.version,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )
