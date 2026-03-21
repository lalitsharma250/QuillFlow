"""
app/api/middleware/auth.py

Unified authentication — supports both API keys and JWT tokens.

Priority:
  1. Try JWT token first (if Bearer token present)
  2. Fall back to API key lookup
  3. Reject if neither works
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.middleware.jwt_auth import get_auth_from_jwt
from app.db.models import ApiKeyRecord, UserRecord, OrganizationRecord
from app.dependencies import get_db_session
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)


async def get_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    jwt_auth: AuthContext | None = Depends(get_auth_from_jwt),
) -> AuthContext:
    """
    Authenticate the request using JWT or API key.

    Checks in order:
      1. JWT token (from get_auth_from_jwt dependency)
      2. API key in Authorization: Bearer header
      3. Reject with 401

    Returns:
        AuthContext with user_id, org_id, email, role
    """
    # ── 1. JWT token (already decoded) ─────────────────
    if jwt_auth is not None:
        return jwt_auth

    # ── 2. API key from Authorization header ───────────
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use 'Bearer <token>' or 'Bearer <api_key>'",
        )

    token = auth_header.removeprefix("Bearer ").strip()

    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    # ── API key lookup ─────────────────────────────────
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    stmt = (
        select(ApiKeyRecord)
        .options(
            selectinload(ApiKeyRecord.user).selectinload(UserRecord.organization)
        )
        .where(
            ApiKeyRecord.key_hash == key_hash,
            ApiKeyRecord.is_active == True,
        )
    )
    result = await session.execute(stmt)
    api_key_record = result.scalar_one_or_none()

    if api_key_record is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    user = api_key_record.user
    org = user.organization

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    if not org.is_active:
        raise HTTPException(status_code=403, detail="Organization is disabled")

    # Check expiration
    if api_key_record.expires_at:
        if api_key_record.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="API key has expired")

    # Update last_used_at
    await session.execute(
        update(ApiKeyRecord)
        .where(ApiKeyRecord.id == api_key_record.id)
        .values(last_used_at=datetime.now(timezone.utc))
    )

    return AuthContext(
        user_id=user.id,
        org_id=org.id,
        email=user.email,
        role=user.role,
        is_superadmin=getattr(user, 'is_superadmin', False),
    )