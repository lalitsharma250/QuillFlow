"""
app/api/v1/users.py

User management endpoints (admin only):
  POST   /v1/admin/users              — Create a new user
  GET    /v1/admin/users              — List users in org
  PATCH  /v1/admin/users/{id}/role    — Change user role
  DELETE /v1/admin/users/{id}         — Deactivate user
  POST   /v1/admin/users/{id}/api-key — Generate API key for user
"""

from __future__ import annotations

import hashlib
import secrets
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.rbac import require_admin
from app.db.models import UserRecord, ApiKeyRecord, OrganizationRecord
from app.db.repository import AuditRepository
from app.dependencies import get_db_session
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/users", tags=["user-management"])


# ── Request/Response Models ────────────────────────────

class CreateUserRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(
        default="viewer",
        description="Role: 'admin', 'editor', or 'viewer'",
    )

    def validate_role(self):
        if self.role not in ("admin", "editor", "viewer"):
            raise ValueError("Role must be 'admin', 'editor', or 'viewer'")
        return self


class UpdateRoleRequest(BaseModel):
    role: str = Field(description="New role: 'admin', 'editor', or 'viewer'")


class UserResponse(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    is_active: bool
    created_at: str


class UserWithKeyResponse(BaseModel):
    user: UserResponse
    api_key: str = Field(description="API key (shown only once)")


class ApiKeyResponse(BaseModel):
    key_id: str
    api_key: str = Field(description="API key (shown only once)")
    name: str


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int


# ── Endpoints ──────────────────────────────────────────

@router.post("", response_model=UserWithKeyResponse)
async def create_user(
    request: CreateUserRequest,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create a new user in the admin's organization.
    Automatically generates an API key for the new user.
    """
    # Validate role
    if request.role not in ("admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin', 'editor', or 'viewer'")

    # Check if email already exists in this org
    existing = await session.execute(
        select(UserRecord).where(
            UserRecord.org_id == auth.org_id,
            UserRecord.email == request.email,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"User with email '{request.email}' already exists")

    # Create user
    user_id = uuid4()
    user = UserRecord(
        id=user_id,
        org_id=auth.org_id,
        email=request.email,
        name=request.name,
        role=request.role,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    # Generate API key
    raw_key = "qf-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = ApiKeyRecord(
        id=uuid4(),
        org_id=auth.org_id,
        user_id=user_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        name=f"Default key for {request.email}",
        is_active=True,
    )
    session.add(api_key)

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="user_created",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=user_id,
        detail={
            "email": request.email,
            "role": request.role,
            "created_by": auth.email,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    logger.info(
        "user_created",
        new_user_id=str(user_id),
        email=request.email,
        role=request.role,
        created_by=auth.email,
    )

    return UserWithKeyResponse(
        user=UserResponse(
            user_id=str(user_id),
            email=request.email,
            name=request.name,
            role=request.role,
            is_active=True,
            created_at=user.created_at.isoformat(),
        ),
        api_key=raw_key,
    )


@router.get("", response_model=UserListResponse)
async def list_users(
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    include_inactive: bool = Query(default=False),
):
    """List all users in the admin's organization."""
    stmt = select(UserRecord).where(UserRecord.org_id == auth.org_id)

    if not include_inactive:
        stmt = stmt.where(UserRecord.is_active == True)

    stmt = stmt.order_by(UserRecord.created_at.desc())

    result = await session.execute(stmt)
    users = result.scalars().all()

    # Count total
    count_stmt = select(func.count(UserRecord.id)).where(
        UserRecord.org_id == auth.org_id
    )
    if not include_inactive:
        count_stmt = count_stmt.where(UserRecord.is_active == True)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar_one()

    return UserListResponse(
        users=[
            UserResponse(
                user_id=str(u.id),
                email=u.email,
                name=u.name,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at.isoformat(),
            )
            for u in users
        ],
        total=total,
    )


@router.patch("/{user_id}/role")
async def update_user_role(
    user_id: UUID,
    request: UpdateRoleRequest,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Change a user's role.
    
    Rules:
      - Cannot change your own role
      - Org admins can only change editor ↔ viewer
      - Org admins cannot promote to admin or demote other admins
      - Superadmin can change any role
      - Must keep at least one admin per org
    """
    if request.role not in ("admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin', 'editor', or 'viewer'")

    if user_id == auth.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own role. Ask another admin.")

    # Find user in same org
    result = await session.execute(
        select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.org_id == auth.org_id,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Non-superadmin restrictions
    if not auth.is_superadmin:
        # Cannot touch admin users
        if user.role == "admin":
            raise HTTPException(
                status_code=403,
                detail="Only super admins can change admin roles. Contact your super admin."
            )
        # Cannot promote to admin
        if request.role == "admin":
            raise HTTPException(
                status_code=403,
                detail="Only super admins can promote users to admin."
            )

    # If demoting an admin, ensure at least one admin remains
    if user.role == "admin" and request.role != "admin":
        admin_count_result = await session.execute(
            select(func.count(UserRecord.id)).where(
                UserRecord.org_id == auth.org_id,
                UserRecord.role == "admin",
                UserRecord.is_active == True,
            )
        )
        admin_count = admin_count_result.scalar_one()

        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote the last admin. Promote another user to admin first."
            )

    old_role = user.role

    await session.execute(
        update(UserRecord)
        .where(UserRecord.id == user_id)
        .values(role=request.role)
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="user_role_changed",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=user_id,
        detail={
            "email": user.email,
            "old_role": old_role,
            "new_role": request.role,
            "changed_by": auth.email,
            "changed_by_superadmin": auth.is_superadmin,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {
        "message": f"Role updated from '{old_role}' to '{request.role}'",
        "user_id": str(user_id),
        "email": user.email,
        "new_role": request.role,
    }

@router.delete("/{user_id}")
async def deactivate_user(
    user_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Deactivate a user (soft delete).
    
    Rules:
      - Cannot deactivate yourself
      - Org admins cannot deactivate other admins (only superadmin can)
      - Superadmin can deactivate anyone except themselves
    """
    if user_id == auth.user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    # Find user
    result = await session.execute(
        select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.org_id == auth.org_id,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Only superadmin can deactivate other admins
    if user.role == "admin" and not auth.is_superadmin:
        raise HTTPException(
            status_code=403,
            detail="Only super admins can deactivate other admins. Contact your super admin."
        )

    # Deactivate user
    await session.execute(
        update(UserRecord)
        .where(UserRecord.id == user_id)
        .values(is_active=False)
    )

    # Deactivate all their API keys
    await session.execute(
        update(ApiKeyRecord)
        .where(ApiKeyRecord.user_id == user_id)
        .values(is_active=False)
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="user_deactivated",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=user_id,
        detail={
            "email": user.email,
            "target_role": user.role,
            "deactivated_by": auth.email,
            "deactivated_by_superadmin": auth.is_superadmin,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {
        "message": f"User '{user.email}' deactivated",
        "user_id": str(user_id),
        "email": user.email,
    }


@router.post("/{user_id}/api-key", response_model=ApiKeyResponse)
async def generate_api_key(
    user_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
    key_name: str = Query(default="Generated key", max_length=200),
):
    """Generate a new API key for a user. Admin only."""
    # Verify user exists in same org
    result = await session.execute(
        select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.org_id == auth.org_id,
            UserRecord.is_active == True,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found or inactive")

    # Generate key
    raw_key = "qf-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = uuid4()

    api_key = ApiKeyRecord(
        id=key_id,
        org_id=auth.org_id,
        user_id=user_id,
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        name=key_name,
        is_active=True,
    )
    session.add(api_key)

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="api_key_created",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="api_key",
        resource_id=key_id,
        detail={
            "for_user": user.email,
            "key_name": key_name,
            "created_by": auth.email,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return ApiKeyResponse(
        key_id=str(key_id),
        api_key=raw_key,
        name=key_name,
    )

@router.patch("/{user_id}/reactivate")
async def reactivate_user(
    user_id: UUID,
    http_request: Request,
    auth: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    """Reactivate a previously deactivated user."""
    result = await session.execute(
        select(UserRecord).where(
            UserRecord.id == user_id,
            UserRecord.org_id == auth.org_id,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_active:
        raise HTTPException(status_code=400, detail="User is already active")

    # Reactivate user
    await session.execute(
        update(UserRecord)
        .where(UserRecord.id == user_id)
        .values(is_active=True)
    )

    # Reactivate their API keys too
    await session.execute(
        update(ApiKeyRecord)
        .where(ApiKeyRecord.user_id == user_id)
        .values(is_active=True)
    )

    # Audit log
    audit = AuditRepository(session)
    await audit.log(
        action="user_reactivated",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="user",
        resource_id=user_id,
        detail={"email": user.email, "reactivated_by": auth.email},
        ip_address=http_request.client.host if http_request.client else None,
    )

    await session.commit()

    return {
        "message": f"User '{user.email}' reactivated",
        "user_id": str(user_id),
        "email": user.email,
    }