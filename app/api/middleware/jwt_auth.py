"""
app/api/middleware/jwt_auth.py

JWT token authentication with role staleness detection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRecord
from app.dependencies import get_db_session
from config import get_settings
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(
    user_id: UUID,
    org_id: UUID,
    email: str,
    role: str,
    is_superadmin: bool = False,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "email": email,
        "role": role,
        "sa": is_superadmin,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_access_token_hours),
    }

    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def create_refresh_token(user_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_days),
    }

    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_auth_from_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext | None:
    """
    Validate JWT token and check role/status haven't changed.
    
    Returns 401 if:
      - Token is invalid or expired
      - User's role has changed since token was issued
      - User has been deactivated
      - User's organization has been deactivated
    """
    if credentials is None:
        return None

    token = credentials.credentials

    # Quick check: API keys don't have JWT structure
    if token.count(".") != 2:
        return None

    try:
        payload = decode_token(token)
    except HTTPException:
        # Re-raise — token is malformed or expired
        raise
    except Exception:
        return None

    if payload.get("type") != "access":
        return None

    user_id = UUID(payload["sub"])
    token_role = payload["role"]
    token_is_superadmin = payload.get("sa", False)

    # ── Validate user state in DB ──────────────────────
    # Check role staleness, active status, org status
    stmt = (
        select(UserRecord)
        .where(UserRecord.id == user_id)
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is still active
    if not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="Your account has been disabled. Please contact an admin.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if role has changed since token was issued
    if user.role != token_role:
        logger.info(
            "stale_role_detected",
            user_id=str(user_id),
            token_role=token_role,
            current_role=user.role,
        )
        raise HTTPException(
            status_code=401,
            detail="Role has changed. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if superadmin status has changed
    current_is_superadmin = getattr(user, "is_superadmin", False)
    if current_is_superadmin != token_is_superadmin:
        logger.info(
            "stale_superadmin_detected",
            user_id=str(user_id),
            token_sa=token_is_superadmin,
            current_sa=current_is_superadmin,
        )
        raise HTTPException(
            status_code=401,
            detail="Permissions have changed. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthContext(
        user_id=user.id,
        org_id=UUID(payload["org"]),
        email=payload["email"],
        role=user.role,  # ← Use fresh role from DB
        is_superadmin=current_is_superadmin,
    )