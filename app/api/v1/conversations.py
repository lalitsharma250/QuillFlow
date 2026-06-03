"""
app/api/v1/conversations.py

Conversation management endpoints.

  POST   /v1/conversations          Create a new conversation
  GET    /v1/conversations          List user's conversations
  GET    /v1/conversations/{id}     Get conversation with messages
  PATCH  /v1/conversations/{id}     Rename conversation
  DELETE /v1/conversations/{id}     Delete conversation
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.rbac import require_viewer
from app.db.repository import ConversationRepository
from app.dependencies import get_db_session
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["conversations"])


# ── Request/Response Models ────────────────────────────

class CreateConversationRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=200)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list = Field(default_factory=list)
    query_type: str | None = None
    cached: bool = False
    created_at: str


class ConversationDetail(BaseModel):
    id: UUID
    title: str
    created_at: str
    updated_at: str
    messages: list[MessageResponse] = Field(default_factory=list)


# ── Endpoints ──────────────────────────────────────────

@router.post("/conversations", response_model=ConversationSummary)
async def create_conversation(
    request: CreateConversationRequest,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """Create a new conversation."""
    repo = ConversationRepository(session)
    conv = await repo.create_conversation(
        org_id=auth.org_id,
        user_id=auth.user_id,
        title=request.title,
    )
    await session.commit()

    return ConversationSummary(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"].isoformat(),
        updated_at=conv["updated_at"].isoformat(),
    )


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """List the current user's conversations (most recent first)."""
    repo = ConversationRepository(session)
    conversations = await repo.list_conversations(
        user_id=auth.user_id,
        org_id=auth.org_id,
    )

    return [
        ConversationSummary(
            id=c["id"],
            title=c["title"],
            created_at=c["created_at"].isoformat(),
            updated_at=c["updated_at"].isoformat(),
        )
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """Get a conversation with all its messages."""
    repo = ConversationRepository(session)
    conv = await repo.get_conversation(
        conversation_id=conversation_id,
        user_id=auth.user_id,
        org_id=auth.org_id,
    )

    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"].isoformat(),
        updated_at=conv["updated_at"].isoformat(),
        messages=[
            MessageResponse(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                sources=m["sources"],
                query_type=m["query_type"],
                cached=m["cached"],
                created_at=m["created_at"].isoformat(),
            )
            for m in conv["messages"]
        ],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: UUID,
    request: RenameConversationRequest,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """Rename a conversation."""
    repo = ConversationRepository(session)
    updated = await repo.rename_conversation(
        conversation_id=conversation_id,
        user_id=auth.user_id,
        org_id=auth.org_id,
        new_title=request.title,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await session.commit()

    # Return updated summary
    conv = await repo.get_conversation(
        conversation_id=conversation_id,
        user_id=auth.user_id,
        org_id=auth.org_id,
    )
    return ConversationSummary(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"].isoformat(),
        updated_at=conv["updated_at"].isoformat(),
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """Delete a conversation and all its messages."""
    repo = ConversationRepository(session)
    deleted = await repo.delete_conversation(
        conversation_id=conversation_id,
        user_id=auth.user_id,
        org_id=auth.org_id,
    )

    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await session.commit()
    return {"status": "deleted", "conversation_id": str(conversation_id)}