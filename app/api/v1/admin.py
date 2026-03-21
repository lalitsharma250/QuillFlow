"""
app/api/v1/admin.py

Admin endpoints for system management.
All endpoints require 'admin' role.

Endpoints:
  DELETE /v1/admin/documents/stale     — Clean up pending/failed documents
  DELETE /v1/admin/documents/{id}      — Delete a specific document + its chunks
  DELETE /v1/admin/cache               — Clear all cached responses
  GET    /v1/admin/stats               — System statistics
  DELETE /v1/admin/jobs/stale          — Clean up orphaned jobs
"""

from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr
from uuid import UUID,uuid4
from datetime import datetime, timezone, timedelta
import string
import random

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.rbac import require_admin, require_superadmin
from app.db.models import (
    DocumentRecord,
    IngestionJobRecord,
    JobDocumentLink,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, update, func
from app.db.models import (
    DocumentRecord,
    IngestionJobRecord,
    JobDocumentLink,
    OrganizationRecord,
    UserRecord,
    ApiKeyRecord,
)
from app.db.repository import AuditRepository, DocumentRepository
from app.dependencies import get_db_session
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════════
# Document Cleanup
# ═══════════════════════════════════════════════════════════


@router.delete("/documents/stale")
async def cleanup_stale_documents(
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    status_filter: str = Query(
        default="pending",
        description="Status to clean up: 'pending', 'failed', or 'all_stale'",
    ),
):
    """
    Delete documents stuck in pending or failed status.
    Also cleans up their job_documents links.
    Only affects documents in the admin's organization.
    """
    # Determine which statuses to clean
    if status_filter == "all_stale":
        statuses = ["pending", "failed"]
    elif status_filter in ("pending", "failed"):
        statuses = [status_filter]
    else:
        raise HTTPException(
            status_code=400,
            detail="status_filter must be 'pending', 'failed', or 'all_stale'",
        )

    # Count before deletion
    count_stmt = (
        select(func.count(DocumentRecord.id))
        .where(
            DocumentRecord.org_id == auth.org_id,
            DocumentRecord.status.in_(statuses),
        )
    )
    result = await session.execute(count_stmt)
    count = result.scalar_one()

    if count == 0:
        return {
            "message": "No stale documents found",
            "deleted_count": 0,
            "statuses_checked": statuses,
        }

    # Get IDs for cleanup
    id_stmt = (
        select(DocumentRecord.id)
        .where(
            DocumentRecord.org_id == auth.org_id,
            DocumentRecord.status.in_(statuses),
        )
    )
    result = await session.execute(id_stmt)
    doc_ids = list(result.scalars().all())

    # Delete job_document links first (foreign key)
    await session.execute(
        delete(JobDocumentLink)
        .where(JobDocumentLink.document_id.in_(doc_ids))
    )

    # Delete documents
    await session.execute(
        delete(DocumentRecord)
        .where(DocumentRecord.id.in_(doc_ids))
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="admin_cleanup_stale_documents",
        user_id=auth.user_id,
        org_id=auth.org_id,
        detail={
            "deleted_count": count,
            "statuses": statuses,
            "document_ids": [str(d) for d in doc_ids[:20]],
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    logger.info(
        "stale_documents_cleaned",
        org_id=str(auth.org_id),
        deleted_count=count,
        statuses=statuses,
    )

    return {
        "message": f"Deleted {count} stale documents",
        "deleted_count": count,
        "statuses_checked": statuses,
    }


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Delete a specific document and its chunks from both Postgres and Qdrant.
    """
    # Verify document exists and belongs to org
    doc_repo = DocumentRepository(session)
    doc = await doc_repo.get_document(document_id, auth.org_id)

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete chunks from Qdrant
    vector_store = getattr(http_request.app.state, "vector_store", None)
    chunks_deleted = 0
    if vector_store:
        try:
            chunks_deleted = await vector_store.delete_by_document_id(
                document_id=document_id,
                org_id=auth.org_id,
            )
        except Exception as e:
            logger.warning(
                "qdrant_delete_failed",
                document_id=str(document_id),
                error=str(e),
            )

    # Delete job_document links
    await session.execute(
        delete(JobDocumentLink)
        .where(JobDocumentLink.document_id == document_id)
    )

    # Delete document record
    await session.execute(
        delete(DocumentRecord)
        .where(
            DocumentRecord.id == document_id,
            DocumentRecord.org_id == auth.org_id,
        )
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="admin_delete_document",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="document",
        resource_id=document_id,
        detail={
            "filename": doc.filename,
            "chunks_deleted": chunks_deleted,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    logger.info(
        "document_deleted",
        document_id=str(document_id),
        filename=doc.filename,
        chunks_deleted=chunks_deleted,
    )

    return {
        "message": f"Document '{doc.filename}' deleted",
        "document_id": str(document_id),
        "filename": doc.filename,
        "chunks_deleted_from_qdrant": chunks_deleted,
    }


# ═══════════════════════════════════════════════════════════
# Cache Management
# ═══════════════════════════════════════════════════════════


@router.delete("/cache")
async def clear_cache(
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Clear all cached responses.
    Useful after re-ingesting documents or updating prompts.
    """
    redis_client = getattr(http_request.app.state, "redis_client", None)

    if redis_client is None:
        return {"message": "Cache not available (Redis not connected)", "cleared": False}

    try:
        await redis_client.flushdb()

        # Audit log
        audit = AuditRepository(session)
        await audit.log(
            action="admin_clear_cache",
            user_id=auth.user_id,
            org_id=auth.org_id,
            ip_address=http_request.client.host if http_request.client else None,
        )
        await session.commit()

        logger.info("cache_cleared", org_id=str(auth.org_id))

        return {"message": "Cache cleared successfully", "cleared": True}

    except Exception as e:
        logger.error("cache_clear_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")


# ═══════════════════════════════════════════════════════════
# Job Cleanup
# ═══════════════════════════════════════════════════════════


@router.delete("/jobs/stale")
async def cleanup_stale_jobs(
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Clean up orphaned ingestion jobs (accepted/processing but never completed).
    """
    # Count stale jobs
    count_stmt = (
        select(func.count(IngestionJobRecord.id))
        .where(
            IngestionJobRecord.org_id == auth.org_id,
            IngestionJobRecord.status.in_(["accepted", "processing"]),
        )
    )
    result = await session.execute(count_stmt)
    count = result.scalar_one()

    if count == 0:
        return {"message": "No stale jobs found", "deleted_count": 0}

    # Delete stale jobs (cascade deletes job_documents links)
    await session.execute(
        delete(IngestionJobRecord)
        .where(
            IngestionJobRecord.org_id == auth.org_id,
            IngestionJobRecord.status.in_(["accepted", "processing"]),
        )
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="admin_cleanup_stale_jobs",
        user_id=auth.user_id,
        org_id=auth.org_id,
        detail={"deleted_count": count},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    logger.info(
        "stale_jobs_cleaned",
        org_id=str(auth.org_id),
        deleted_count=count,
    )

    return {
        "message": f"Deleted {count} stale jobs",
        "deleted_count": count,
    }


# ═══════════════════════════════════════════════════════════
# System Statistics
# ═══════════════════════════════════════════════════════════


@router.get("/stats")
async def get_system_stats(
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Get system statistics scoped to the admin's organization."""
    org_id = auth.org_id

    # Document counts by status
    doc_stats_stmt = (
        select(
            DocumentRecord.status,
            func.count(DocumentRecord.id),
        )
        .where(DocumentRecord.org_id == org_id)
        .group_by(DocumentRecord.status)
    )
    result = await session.execute(doc_stats_stmt)
    doc_stats = {row[0]: row[1] for row in result.all()}

    # Total chunks
    total_chunks_stmt = (
        select(func.sum(DocumentRecord.chunk_count))
        .where(
            DocumentRecord.org_id == org_id,
            DocumentRecord.status == "indexed",
        )
    )
    result = await session.execute(total_chunks_stmt)
    total_chunks = result.scalar_one() or 0

    # Job counts
    job_stats_stmt = (
        select(
            IngestionJobRecord.status,
            func.count(IngestionJobRecord.id),
        )
        .where(IngestionJobRecord.org_id == org_id)
        .group_by(IngestionJobRecord.status)
    )
    result = await session.execute(job_stats_stmt)
    job_stats = {row[0]: row[1] for row in result.all()}

    # Qdrant stats — count only this org's vectors
    vector_store = getattr(http_request.app.state, "vector_store", None)
    qdrant_stats = {}
    if vector_store:
        try:
            org_count = await vector_store._count_by_org(org_id)
            collection_info = await vector_store.get_collection_info()
            qdrant_stats = {
                "org_points": org_count,
                "status": collection_info.get("status", "unknown"),
            }
        except Exception as e:
            qdrant_stats = {"org_points": 0, "status": "error", "error": str(e)}

    # Cache stats — count only this org's cache keys
    redis_client = getattr(http_request.app.state, "redis_client", None)
    cache_stats = {}
    if redis_client:
        try:
            # Count keys matching this org's pattern
            org_pattern = f"*{str(org_id)[:8]}*"
            org_keys = 0
            async for _ in redis_client.scan_iter(match=org_pattern, count=100):
                org_keys += 1

            info = await redis_client.info("memory")
            cache_stats = {
                "org_keys": org_keys,
                "memory_used": info.get("used_memory_human", "unknown"),
            }
        except Exception:
            cache_stats = {"org_keys": 0, "memory_used": "unknown"}

    return {
        "organization": {
            "org_id": str(org_id),
        },
        "documents": {
            "by_status": doc_stats,
            "total_indexed": doc_stats.get("indexed", 0),
            "total_chunks": total_chunks,
        },
        "jobs": {
            "by_status": job_stats,
        },
        "vector_store": qdrant_stats,
        "cache": cache_stats,
    }
# ═══════════════════════════════════════════════════════════
# Organization Management
# ═══════════════════════════════════════════════════════════

# Note: Org creation is a "superadmin" operation.
# In a real multi-tenant SaaS, you'd have a separate superadmin role.
# For now, any admin can create orgs (useful for development).

class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)


class OrgResponse(BaseModel):
    org_id: str
    name: str
    is_active: bool
    settings: dict
    created_at: str
    user_count: int = 0
    document_count: int = 0


class OrgWithAdminResponse(BaseModel):
    organization: OrgResponse
    admin_user: dict
    admin_api_key: str = Field(description="Admin API key (shown only once)")


@router.post("/orgs", response_model=OrgWithAdminResponse)
async def create_organization(
    request: CreateOrgRequest,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create a new organization with an admin user.
    
    This creates:
      1. Organization record
      2. Admin user for the new org
      3. API key for the admin user
    
    The creating admin (from the current org) is NOT added to the new org.
    """
    import hashlib
    import secrets
    from uuid import uuid4
    from app.db.models import OrganizationRecord, UserRecord, ApiKeyRecord

    # Check if org name already exists
    existing = await session.execute(
        select(OrganizationRecord).where(OrganizationRecord.name == request.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Organization '{request.name}' already exists")

    # 1. Create organization
    org_id = uuid4()
    org = OrganizationRecord(
        id=org_id,
        name=request.name,
        is_active=True,
        settings=request.settings,
    )
    session.add(org)
    await session.flush()

    # 2. Create admin user for new org
    admin_user_id = uuid4()
    admin_email = f"admin@{request.name.lower().replace(' ', '-')}.local"
    admin_user = UserRecord(
        id=admin_user_id,
        org_id=org_id,
        email=admin_email,
        name=f"{request.name} Admin",
        role="admin",
        is_active=True,
    )
    session.add(admin_user)
    await session.flush()

    # 3. Create API key for admin
    raw_key = "qf-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = ApiKeyRecord(
        id=uuid4(),
        org_id=org_id,
        user_id=admin_user_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        name=f"Admin key for {request.name}",
        is_active=True,
    )
    session.add(api_key)

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="org_created",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="organization",
        resource_id=org_id,
        detail={
            "new_org_name": request.name,
            "new_org_id": str(org_id),
            "created_by": auth.email,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    logger.info(
        "organization_created",
        org_id=str(org_id),
        org_name=request.name,
        created_by=auth.email,
    )

    return OrgWithAdminResponse(
        organization=OrgResponse(
            org_id=str(org_id),
            name=request.name,
            is_active=True,
            settings=request.settings,
            created_at=org.created_at.isoformat(),
            user_count=1,
            document_count=0,
        ),
        admin_user={
            "user_id": str(admin_user_id),
            "email": admin_email,
            "name": admin_user.name,
            "role": "admin",
        },
        admin_api_key=raw_key,
    )


@router.get("/orgs", response_model=list[OrgResponse])
async def list_organizations(
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    List all organizations.
    Includes user count and document count for each.
    """
    from app.db.models import OrganizationRecord, UserRecord, DocumentRecord

    # Get all orgs
    result = await session.execute(
        select(OrganizationRecord).order_by(OrganizationRecord.created_at.desc())
    )
    orgs = result.scalars().all()

    response = []
    for org in orgs:
        # User count
        user_count_result = await session.execute(
            select(func.count(UserRecord.id)).where(
                UserRecord.org_id == org.id,
                UserRecord.is_active == True,
            )
        )
        user_count = user_count_result.scalar_one()

        # Document count
        doc_count_result = await session.execute(
            select(func.count(DocumentRecord.id)).where(
                DocumentRecord.org_id == org.id,
                DocumentRecord.status == "indexed",
            )
        )
        doc_count = doc_count_result.scalar_one()

        response.append(OrgResponse(
            org_id=str(org.id),
            name=org.name,
            is_active=org.is_active,
            settings=org.settings or {},
            created_at=org.created_at.isoformat(),
            user_count=user_count,
            document_count=doc_count,
        ))

    return response


@router.patch("/orgs/{org_id}")
async def update_organization(
    org_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    name: str | None = None,
    is_active: bool | None = None,
):
    """Update organization name or active status."""
    from app.db.models import OrganizationRecord

    result = await session.execute(
        select(OrganizationRecord).where(OrganizationRecord.id == org_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    updates = {}
    if name is not None:
        updates["name"] = name
    if is_active is not None:
        updates["is_active"] = is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    await session.execute(
        update(OrganizationRecord)
        .where(OrganizationRecord.id == org_id)
        .values(**updates)
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="org_updated",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="organization",
        resource_id=org_id,
        detail={"updates": updates, "updated_by": auth.email},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {"message": "Organization updated", "org_id": str(org_id), "updates": updates}


# ═══════════════════════════════════════════════════════════
# API Key Management
# ═══════════════════════════════════════════════════════════


class ApiKeyListItem(BaseModel):
    key_id: str
    key_prefix: str
    name: str
    is_active: bool
    last_used_at: str | None
    created_at: str


@router.get("/users/{user_id}/api-keys", response_model=list[ApiKeyListItem])
async def list_user_api_keys(
    user_id: UUID,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    List all API keys for a user.
    Shows key prefix (first 8 chars) but NOT the full key.
    """
    from app.db.models import ApiKeyRecord, UserRecord

    # Verify user is in same org
    user_result = await session.execute(
        select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.org_id == auth.org_id,
        )
    )
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        select(ApiKeyRecord)
        .where(ApiKeyRecord.user_id == user_id)
        .order_by(ApiKeyRecord.created_at.desc())
    )
    keys = result.scalars().all()

    return [
        ApiKeyListItem(
            key_id=str(k.id),
            key_prefix=k.key_prefix,
            name=k.name,
            is_active=k.is_active,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            created_at=k.created_at.isoformat(),
        )
        for k in keys
    ]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Revoke (deactivate) an API key."""
    from app.db.models import ApiKeyRecord

    result = await session.execute(
        select(ApiKeyRecord).where(
            ApiKeyRecord.id == key_id,
            ApiKeyRecord.org_id == auth.org_id,
        )
    )
    key = result.scalar_one_or_none()

    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    await session.execute(
        update(ApiKeyRecord)
        .where(ApiKeyRecord.id == key_id)
        .values(is_active=False)
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="api_key_revoked",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="api_key",
        resource_id=key_id,
        detail={
            "key_prefix": key.key_prefix,
            "key_name": key.name,
            "revoked_by": auth.email,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {
        "message": f"API key '{key.name}' revoked",
        "key_id": str(key_id),
        "key_prefix": key.key_prefix,
    }


# ═══════════════════════════════════════════════════════════
# Audit Log Viewer
# ═══════════════════════════════════════════════════════════


@router.get("/audit")
async def get_audit_logs(
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    action: str | None = Query(default=None, description="Filter by action type"),
    user_id: UUID | None = Query(default=None, description="Filter by user"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    View audit logs for the organization.
    
    Action types:
      auth: auth_success, auth_failure
      query: query, query_cached
      ingest: ingest_single, ingest_bulk
      admin: user_created, user_deactivated, user_role_changed,
             api_key_created, api_key_revoked, org_created,
             admin_clear_cache, admin_cleanup_stale_documents
    """
    audit = AuditRepository(session)

    if user_id:
        logs = await audit.get_user_activity(user_id, limit=limit)
    else:
        logs = await audit.get_org_activity(
            auth.org_id,
            action_filter=action,
            limit=limit,
        )

    return {
        "logs": logs,
        "count": len(logs),
        "filters": {
            "action": action,
            "user_id": str(user_id) if user_id else None,
            "limit": limit,
        },
    }


# ═══════════════════════════════════════════════════════════
# Invite Code Management
# ═══════════════════════════════════════════════════════════


def generate_invite_code() -> str:
    """Generate a readable invite code like INV-a8f3b2c1."""
    chars = string.ascii_lowercase + string.digits
    random_part = "".join(random.choices(chars, k=8))
    return f"INV-{random_part}"


class CreateInviteRequest(BaseModel):
    role: str = Field(
        default="viewer",
        description="Role for users who use this code: 'viewer' or 'editor'",
    )
    max_uses: int = Field(default=10, ge=1, le=1000)
    expires_in_days: int = Field(
        default=7, ge=1, le=90,
        description="Code expires after this many days",
    )


class InviteCodeResponse(BaseModel):
    code: str
    role: str
    max_uses: int
    times_used: int
    is_active: bool
    expires_at: str
    created_at: str


@router.post("/invites", response_model=InviteCodeResponse)
async def create_invite_code(
    request: CreateInviteRequest,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Generate an invite code for your organization.
    Share this code with people you want to join.
    """
    from app.db.models import InviteCodeRecord
    from datetime import timedelta

    if request.role not in ("viewer", "editor"):
        raise HTTPException(status_code=400, detail="Role must be 'viewer' or 'editor'")

    # Generate unique code
    code = generate_invite_code()

    # Ensure uniqueness
    existing = await session.execute(
        select(InviteCodeRecord).where(InviteCodeRecord.code == code)
    )
    while existing.scalar_one_or_none():
        code = generate_invite_code()
        existing = await session.execute(
            select(InviteCodeRecord).where(InviteCodeRecord.code == code)
        )

    expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_in_days)

    invite = InviteCodeRecord(
        id=uuid4(),
        org_id=auth.org_id,
        code=code,
        role=request.role,
        created_by=auth.user_id,
        max_uses=request.max_uses,
        expires_at=expires_at,
    )
    session.add(invite)

    # Audit
    audit_repo = AuditRepository(session)
    await audit_repo.log(
        action="invite_code_created",
        user_id=auth.user_id,
        org_id=auth.org_id,
        detail={
            "code": code,
            "role": request.role,
            "max_uses": request.max_uses,
            "expires_in_days": request.expires_in_days,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return InviteCodeResponse(
        code=code,
        role=request.role,
        max_uses=request.max_uses,
        times_used=0,
        is_active=True,
        expires_at=expires_at.isoformat(),
        created_at=invite.created_at.isoformat(),
    )


@router.get("/invites", response_model=list[InviteCodeResponse])
async def list_invite_codes(
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    include_expired: bool = Query(default=False),
):
    """List all invite codes for your organization."""
    from app.db.models import InviteCodeRecord

    stmt = select(InviteCodeRecord).where(InviteCodeRecord.org_id == auth.org_id)

    if not include_expired:
        stmt = stmt.where(
            InviteCodeRecord.is_active == True,
            InviteCodeRecord.expires_at > datetime.now(timezone.utc),
        )

    stmt = stmt.order_by(InviteCodeRecord.created_at.desc())

    result = await session.execute(stmt)
    invites = result.scalars().all()

    return [
        InviteCodeResponse(
            code=inv.code,
            role=inv.role,
            max_uses=inv.max_uses,
            times_used=inv.times_used,
            is_active=inv.is_active,
            expires_at=inv.expires_at.isoformat(),
            created_at=inv.created_at.isoformat(),
        )
        for inv in invites
    ]


@router.delete("/invites/{code}")
async def revoke_invite_code(
    code: str,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Revoke an invite code so it can no longer be used."""
    from app.db.models import InviteCodeRecord

    result = await session.execute(
        select(InviteCodeRecord).where(
            InviteCodeRecord.code == code,
            InviteCodeRecord.org_id == auth.org_id,
        )
    )
    invite = result.scalar_one_or_none()

    if invite is None:
        raise HTTPException(status_code=404, detail="Invite code not found")

    await session.execute(
        update(InviteCodeRecord)
        .where(InviteCodeRecord.id == invite.id)
        .values(is_active=False)
    )

    audit_repo = AuditRepository(session)
    await audit_repo.log(
        action="invite_code_revoked",
        user_id=auth.user_id,
        org_id=auth.org_id,
        detail={"code": code, "revoked_by": auth.email},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {"message": f"Invite code '{code}' revoked", "code": code}

from app.api.middleware.rbac import require_admin, require_superadmin


# ═══════════════════════════════════════════════════════════
# Super Admin — Organization Management
# ═══════════════════════════════════════════════════════════

@router.get("/superadmin/orgs")
async def superadmin_list_orgs(
    auth: AuthContext = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db_session),
):
    """List ALL organizations with user and document counts. Superadmin only."""
    result = await session.execute(
        select(OrganizationRecord).order_by(OrganizationRecord.created_at.desc())
    )
    orgs = result.scalars().all()

    response = []
    for org in orgs:
        user_count = (await session.execute(
            select(func.count(UserRecord.id)).where(
                UserRecord.org_id == org.id, UserRecord.is_active == True
            )
        )).scalar_one()

        doc_count = (await session.execute(
            select(func.count(DocumentRecord.id)).where(
                DocumentRecord.org_id == org.id, DocumentRecord.status == "indexed"
            )
        )).scalar_one()

        response.append({
            "org_id": str(org.id),
            "name": org.name,
            "is_active": org.is_active,
            "user_count": user_count,
            "document_count": doc_count,
            "created_at": org.created_at.isoformat(),
        })

    return response


@router.get("/superadmin/orgs/{org_id}/users")
async def superadmin_list_org_users(
    org_id: UUID,
    auth: AuthContext = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db_session),
):
    """List all users in a specific organization. Superadmin only."""
    result = await session.execute(
        select(UserRecord)
        .where(UserRecord.org_id == org_id)
        .order_by(UserRecord.created_at.desc())
    )
    users = result.scalars().all()

    return [
        {
            "user_id": str(u.id),
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "is_active": u.is_active,
            "is_superadmin": u.is_superadmin,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@router.delete("/superadmin/orgs/{org_id}")
async def superadmin_deactivate_org(
    org_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db_session),
):
    """Deactivate an organization. All users lose access. Superadmin only."""
    result = await session.execute(
        select(OrganizationRecord).where(OrganizationRecord.id == org_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    await session.execute(
        update(OrganizationRecord).where(OrganizationRecord.id == org_id).values(is_active=False)
    )

    # Audit
    audit = AuditRepository(session)
    await audit.log(
        action="org_deactivated",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="organization",
        resource_id=org_id,
        detail={"org_name": org.name, "deactivated_by": auth.email},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {"message": f"Organization '{org.name}' deactivated", "org_id": str(org_id)}


@router.patch("/superadmin/orgs/{org_id}/reactivate")
async def superadmin_reactivate_org(
    org_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db_session),
):
    """Reactivate a deactivated organization. Superadmin only."""
    await session.execute(
        update(OrganizationRecord).where(OrganizationRecord.id == org_id).values(is_active=True)
    )

    audit = AuditRepository(session)
    await audit.log(
        action="org_reactivated",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="organization",
        resource_id=org_id,
        detail={"reactivated_by": auth.email},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {"message": "Organization reactivated", "org_id": str(org_id)}

class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    admin_email: EmailStr
    admin_name: str = Field(min_length=1, max_length=200)
    admin_password: str = Field(min_length=8, max_length=128)


@router.post("/superadmin/orgs")
async def superadmin_create_org(
    request: CreateOrgRequest,
    http_request: Request,
    auth: AuthContext = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create a new organization with its first admin user.
    Superadmin only.
    """
    import hashlib
    import secrets
    import bcrypt

    # Check org name uniqueness
    existing = await session.execute(
        select(OrganizationRecord).where(OrganizationRecord.name == request.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Organization '{request.name}' already exists")

    # Check email uniqueness
    existing_user = await session.execute(
        select(UserRecord).where(UserRecord.email == request.admin_email)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Email '{request.admin_email}' already registered")

    # Create organization
    org_id = uuid4()
    org = OrganizationRecord(
        id=org_id,
        name=request.name,
        is_active=True,
    )
    session.add(org)
    await session.flush()

    # Create admin user
    admin_user_id = uuid4()
    password_hash = bcrypt.hashpw(request.admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    admin_user = UserRecord(
        id=admin_user_id,
        org_id=org_id,
        email=request.admin_email,
        name=request.admin_name,
        password_hash=password_hash,
        role="admin",
        is_active=True,
    )
    session.add(admin_user)

    # Create API key for admin
    raw_key = "qf-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = ApiKeyRecord(
        id=uuid4(),
        org_id=org_id,
        user_id=admin_user_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        name=f"Admin key for {request.name}",
        is_active=True,
    )
    session.add(api_key)

    # Audit
    audit_repo = AuditRepository(session)
    await audit_repo.log(
        action="org_created",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="organization",
        resource_id=org_id,
        detail={
            "new_org_name": request.name,
            "admin_email": request.admin_email,
            "created_by": auth.email,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {
        "message": f"Organization '{request.name}' created",
        "org_id": str(org_id),
        "org_name": request.name,
        "admin": {
            "user_id": str(admin_user_id),
            "email": request.admin_email,
            "name": request.admin_name,
        },
        "api_key": raw_key,
    }