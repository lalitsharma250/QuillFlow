"""
app/api/middleware/jwt_auth.py

JWT token authentication.

Flow:
  1. POST /v1/auth/login — exchange API key for JWT tokens
  2. POST /v1/auth/refresh — exchange refresh token for new access token
  3. All other endpoints — verify JWT in Authorization header

Token structure:
  Access token:  Short-lived (1 hour), contains user_id, org_id, role
  Refresh token: Long-lived (7 days), contains user_id only
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

# FastAPI security scheme — extracts Bearer token from header
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
        "sa": is_superadmin,  # ← superadmin flag
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_access_token_hours),
    }

    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def create_refresh_token(user_id: UUID) -> str:
    """
    Create a long-lived refresh token.
    Contains minimal info — only used to get a new access token.
    """
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
    """
    Decode and verify a JWT token.
    Raises HTTPException if invalid or expired.
    """
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
) -> AuthContext | None:
    if credentials is None:
        return None

    token = credentials.credentials

    if token.count(".") != 2:
        return None

    try:
        payload = decode_token(token)
    except Exception:
        return None

    if payload.get("type") != "access":
        return None

    return AuthContext(
        user_id=UUID(payload["sub"]),
        org_id=UUID(payload["org"]),
        email=payload["email"],
        role=payload["role"],
        is_superadmin=payload.get("sa", False),
    )