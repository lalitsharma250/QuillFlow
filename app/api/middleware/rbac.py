"""
app/api/middleware/rbac.py

Role-based access control helpers.
Thin wrappers around AuthContext for clean endpoint declarations.

Usage:
    @router.post("/ingest")
    async def ingest(auth: AuthContext = Depends(require_editor)):
        ...  # Only admin and editor can reach here
"""

from fastapi import Depends, HTTPException

from app.api.middleware.auth import get_auth_context
from app.models.domain import AuthContext


async def require_viewer(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Any authenticated user (viewer, editor, admin)."""
    return auth


async def require_editor(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Editor or admin only. Viewers are rejected."""
    if not auth.can_ingest():
        raise HTTPException(
            status_code=403,
            detail="This action requires 'editor' or 'admin' role",
        )
    return auth


async def require_admin(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Require org admin OR superadmin."""
    if auth.role != "admin" and not auth.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth


async def require_superadmin(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Require superadmin only."""
    if not auth.is_superadmin:
        raise HTTPException(status_code=403, detail="Super admin access required")
    return auth