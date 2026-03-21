"""
app/api/router.py

Top-level API router. Mounts all versioned sub-routers.
"""

from fastapi import APIRouter

from app.api.v1 import chat, documents, health, ingest
from app.api.v1.auth import router as auth_router
from app.api.v1.admin import router as admin_router
from app.api.v1.users import router as users_router


api_router = APIRouter()

# ── System endpoints (no version prefix) ───────────────
api_router.include_router(
    health.router,
    prefix="/v1",
)

# ── V1 endpoints ───────────────────────────────────────
api_router.include_router(
    chat.router,
    prefix="/v1",
)

api_router.include_router(
    ingest.router,
    prefix="/v1",
)

api_router.include_router(
    documents.router,
    prefix="/v1",
)

api_router.include_router(auth_router, prefix="/v1")
api_router.include_router(admin_router, prefix="/v1")
api_router.include_router(users_router, prefix="/v1")

# ── Basic liveness probe (no auth required) ────────────
@api_router.get("/health", tags=["system"])
async def liveness():
    """Basic liveness check — proves the process is running."""
    return {"status": "alive", "service": "quillflow"}