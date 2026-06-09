"""
app/api/v1/chat.py

POST /v1/chat — Main query endpoint with conversation persistence.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.auth import get_auth_context
from app.api.middleware.rbac import require_viewer
from app.dependencies import (
    get_compiled_graph,
    get_db_session,
)
from app.db.repository import AuditRepository, LineageRepository, ConversationRepository
from app.graph.builder import create_initial_state
from app.models.domain import AuthContext
from app.models.requests import ChatRequest
from app.models.responses import (
    ChatResponse,
    EvalScoreSummary,
    SourceReference,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from config.constants import SSE_KEEPALIVE_INTERVAL
from app.api.middleware.rate_limit import RateLimiter
from config.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["chat"])
settings = get_settings()


async def _resolve_conversation(
    repo: ConversationRepository,
    conversation_id: str | None,
    auth: AuthContext,
) -> UUID:
    """
    Resolve or create a conversation.
    Returns the conversation UUID.
    """
    if conversation_id:
        try:
            conv_uuid = UUID(conversation_id)
        except ValueError:
            # Invalid UUID — create new
            conv = await repo.create_conversation(
                org_id=auth.org_id, user_id=auth.user_id
            )
            return conv["id"]

        # Verify ownership
        owns = await repo.verify_ownership(
            conversation_id=conv_uuid,
            user_id=auth.user_id,
            org_id=auth.org_id,
        )
        if owns:
            return conv_uuid

    # No ID or not owned — create new conversation
    conv = await repo.create_conversation(
        org_id=auth.org_id, user_id=auth.user_id
    )
    return conv["id"]


async def _build_history_from_db(
    repo: ConversationRepository,
    conversation_id: UUID,
) -> list[dict]:
    """
    Build conversation history from DB.
    Returns list of {role, content} dicts in chronological order.
    """
    messages = await repo.get_recent_messages(
        conversation_id=conversation_id,
        limit=20,
    )
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    _rate_limit: None = Depends(RateLimiter("chat")),
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Main query endpoint with conversation persistence.

    - Resolves/creates conversation
    - Builds history from DB (single source of truth)
    - Persists user + assistant messages
    - Streams or returns complete response
    """
    compiled_graph = getattr(http_request.app.state, "compiled_graph", None)
    if compiled_graph is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready — graph not compiled",
        )

    response_id = str(uuid4())
    conv_repo = ConversationRepository(session)

    # ── Resolve or create conversation ─────────────────
    conversation_id = await _resolve_conversation(
        conv_repo, request.conversation_id, auth
    )

    # ── Build history from DB (BEFORE saving current msg) ──
    history = await _build_history_from_db(conv_repo, conversation_id)

    # ── Save user message ──────────────────────────────
    await conv_repo.add_message(
        conversation_id=conversation_id,
        role="user",
        content=request.query,
    )
    await session.commit()

    # ── Audit log ──────────────────────────────────────
    audit = AuditRepository(session)
    await audit.log(
        action="query",
        user_id=auth.user_id,
        org_id=auth.org_id,
        resource_type="chat",
        resource_id=UUID(response_id),
        detail={
            "query_preview": request.query[:200],
            "conversation_id": str(conversation_id),
            "stream": request.stream,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )
    await session.commit()

    # ── Create initial state (history from DB) ─────────
    initial_state = create_initial_state(
        query=request.query,
        auth=auth,
        conversation_id=str(conversation_id),
        model_preference=request.model_preference,
        include_sources=request.include_sources,
        max_sections=request.max_sections,
        stream=request.stream,
        response_id=response_id,
        history=history,  # ← From DB, not frontend
    )

    if request.stream:
        return StreamingResponse(
            _stream_response(
                compiled_graph, initial_state, auth, response_id,
                session, conversation_id, conv_repo,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await _complete_response(
            compiled_graph, initial_state, auth, response_id,
            session, request, conversation_id, conv_repo,
        )


async def _complete_response(
    graph,
    initial_state: dict,
    auth: AuthContext,
    response_id: str,
    session: AsyncSession,
    request: ChatRequest,
    conversation_id: UUID,
    conv_repo: ConversationRepository,
) -> ChatResponse:
    """Non-streaming mode."""
    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.error("graph_execution_failed", error=str(e), response_id=response_id)
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)[:200]}")

    error = final_state.get("error")
    if error and not final_state.get("final_output"):
        raise HTTPException(status_code=422, detail=error)

    # ── Handle cache hit ───────────────────────────────
    if final_state.get("cache_hit") and final_state.get("cached_response"):
        cached = final_state["cached_response"]
        content = cached.get("content", "")
        query_type = cached.get("query_type", "simple")
        sources_data = cached.get("sources", [])

        # Persist assistant message
        await conv_repo.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            sources=sources_data,
            query_type=query_type,
            cached=True,
        )
        await session.commit()

        return ChatResponse(
            response_id=UUID(response_id),
            content=content,
            query_type=query_type,
            sources=[
                SourceReference(**s) for s in sources_data
            ] if request.include_sources else [],
            usage=TokenUsage(),
            cached=True,
            conversation_id=str(conversation_id),
        )

    # ── Build response from final state ────────────────
    content = final_state.get("final_output", "")
    query_type = final_state.get("query_type", "simple")
    if hasattr(query_type, "value"):
        query_type = query_type.value

    chunks = final_state.get("retrieved_chunks", [])
    total_usage = final_state.get("total_usage") or TokenUsage()
    eval_scores = final_state.get("eval_scores")

    sources = [
        SourceReference(
            filename=rc.chunk.metadata.source_filename,
            page_number=rc.chunk.metadata.page_number,
            section_heading=rc.chunk.metadata.section_heading,
            chunk_text_preview=rc.chunk.text[:200],
            relevance_score=rc.score,
        )
        for rc in chunks[:10]
    ]

    # ── Persist assistant message ──────────────────────
    sources_dicts = [s.model_dump() for s in sources]
    await conv_repo.add_message(
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        sources=sources_dicts,
        query_type=query_type,
        cached=False,
    )
    await session.commit()

    await _record_lineage(session, response_id, request.query, chunks)

    eval_summary = None
    if eval_scores:
        eval_summary = EvalScoreSummary(
            faithfulness=eval_scores.faithfulness,
            relevancy=eval_scores.answer_relevancy,
        )

    return ChatResponse(
        response_id=UUID(response_id),
        content=content,
        query_type=query_type,
        sources=sources,
        usage=total_usage,
        eval_scores=eval_summary,
        cached=False,
        conversation_id=str(conversation_id),
    )


async def _stream_response(
    graph,
    initial_state: dict,
    auth: AuthContext,
    response_id: str,
    session: AsyncSession,
    conversation_id: UUID,
    conv_repo: ConversationRepository,
):
    """Streaming mode with message persistence."""
    # ── Stream start (include conversation_id) ─────────
    yield StreamEvent(
        type=StreamEventType.STREAM_START,
        response_id=UUID(response_id),
        conversation_id=str(conversation_id),
    ).to_sse()

    try:
        final_state = None

        async for state_update in graph.astream(initial_state):
            for node_name, node_output in state_update.items():
                if node_output is None:
                    continue

                if final_state is None:
                    final_state = {**initial_state, **node_output}
                else:
                    final_state.update(node_output)

                async for event in _node_to_events(node_name, node_output, final_state):
                    yield event

        if final_state is None:
            final_state = initial_state

        error = final_state.get("error")
        if error and not final_state.get("final_output"):
            yield StreamEvent(
                type=StreamEventType.ERROR,
                error_detail=error,
            ).to_sse()
            return

        # ── Build sources ──────────────────────────────
        total_usage = final_state.get("total_usage") or TokenUsage()
        is_cached = final_state.get("cache_hit", False)
        sources: list[SourceReference] = []

        if is_cached and final_state.get("cached_response"):
            cached = final_state["cached_response"]
            sources = [SourceReference(**s) for s in cached.get("sources", [])]
        else:
            chunks = final_state.get("retrieved_chunks", [])
            sources = [
                SourceReference(
                    filename=rc.chunk.metadata.source_filename,
                    page_number=rc.chunk.metadata.page_number,
                    section_heading=rc.chunk.metadata.section_heading,
                    chunk_text_preview=rc.chunk.text[:200],
                    relevance_score=rc.score,
                )
                for rc in chunks[:10]
            ]

        # ── Stream end ─────────────────────────────────
        yield StreamEvent(
            type=StreamEventType.STREAM_END,
            sources=sources,
            usage=total_usage,
            cached=is_cached,
            conversation_id=str(conversation_id),
        ).to_sse()

        # ── Persist assistant message ──────────────────
        final_content = final_state.get("final_output", "")
        if is_cached and final_state.get("cached_response"):
            final_content = final_state["cached_response"].get("content", "")

        query_type = final_state.get("query_type", "simple")
        if hasattr(query_type, "value"):
            query_type = query_type.value

        sources_dicts = [s.model_dump() for s in sources]

        await conv_repo.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=final_content,
            sources=sources_dicts,
            query_type=query_type,
            cached=is_cached,
        )
        await session.commit()

        # ── Record lineage (fresh retrieval only) ──────
        if not is_cached:
            query = final_state.get("query", "")
            chunks = final_state.get("retrieved_chunks", [])
            await _record_lineage(session, response_id, query, chunks)

    except Exception as e:
        logger.error("stream_error", error=str(e), response_id=response_id)
        yield StreamEvent(
            type=StreamEventType.ERROR,
            error_detail=f"Generation failed: {str(e)[:200]}",
        ).to_sse()


async def _node_to_events(
    node_name: str,
    node_output: dict,
    full_state: dict,
):
    """Convert a node's output into SSE events."""
    from config.constants import (
        NODE_INPUT_FILTER,
        NODE_CACHE_CHECK,
        NODE_ROUTER,
        NODE_RETRIEVER,
        NODE_PLANNER,
        NODE_WRITER,
        NODE_REDUCER,
        NODE_VALIDATOR,
    )

    if node_name == NODE_INPUT_FILTER:
        if node_output.get("error"):
            yield StreamEvent(
                type=StreamEventType.ERROR,
                error_detail=node_output["error"],
            ).to_sse()
        else:
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message="Input validated",
            ).to_sse()

    elif node_name == NODE_CACHE_CHECK:
        if node_output.get("cache_hit"):
            cached = node_output.get("cached_response", {})
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message="⚡ Found in cache",
            ).to_sse()
            yield StreamEvent(
                type=StreamEventType.CONTENT_DELTA,
                content=cached.get("content", ""),
            ).to_sse()
        else:
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message="Searching knowledge base...",
            ).to_sse()

    elif node_name == NODE_ROUTER:
        query_type = node_output.get("query_type", "simple")
        qt_value = query_type.value if hasattr(query_type, "value") else str(query_type)
        yield StreamEvent(
            type=StreamEventType.STATUS_UPDATE,
            message=f"Query classified as {qt_value}",
            query_type=qt_value,
        ).to_sse()

    elif node_name == NODE_RETRIEVER:
        chunks = node_output.get("retrieved_chunks", [])
        yield StreamEvent(
            type=StreamEventType.STATUS_UPDATE,
            message=f"Retrieved {len(chunks)} relevant passages",
        ).to_sse()

    elif node_name == NODE_PLANNER:
        plan = node_output.get("plan")
        if plan:
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message=f"Planned {len(plan.sections)} sections: {plan.title}",
            ).to_sse()

    elif node_name == NODE_WRITER:
        drafts = node_output.get("section_drafts", [])
        for draft in drafts:
            yield StreamEvent(
                type=StreamEventType.SECTION_START,
                heading=draft.heading,
            ).to_sse()
            yield StreamEvent(
                type=StreamEventType.CONTENT_DELTA,
                content=draft.content,
            ).to_sse()
            yield StreamEvent(
                type=StreamEventType.SECTION_END,
                heading=draft.heading,
                word_count=draft.word_count,
            ).to_sse()

    elif node_name == NODE_REDUCER:
        final_output = node_output.get("final_output", "")
        if final_output and not full_state.get("section_drafts"):
            yield StreamEvent(
                type=StreamEventType.CONTENT_DELTA,
                content=final_output,
            ).to_sse()
        elif final_output and full_state.get("section_drafts"):
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message="Polishing final document...",
            ).to_sse()
            yield StreamEvent(
                type=StreamEventType.CONTENT_DELTA,
                content=final_output,
            ).to_sse()

    elif node_name == NODE_VALIDATOR:
        is_approved = node_output.get("is_approved", False)
        if not is_approved:
            reasons = node_output.get("validation_result")
            detail = ""
            if reasons and hasattr(reasons, "rejection_reasons"):
                detail = "; ".join(reasons.rejection_reasons)
            yield StreamEvent(
                type=StreamEventType.STATUS_UPDATE,
                message=f"Quality check: {'passed' if is_approved else 'flagged'}" +
                        (f" — {detail}" if detail else ""),
            ).to_sse()


async def _record_lineage(
    session: AsyncSession,
    response_id: str,
    query: str,
    chunks: list,
) -> None:
    """Record which chunks were used for this response."""
    if not chunks:
        return

    try:
        lineage_repo = LineageRepository(session)
        await lineage_repo.record_lineage(
            response_id=UUID(response_id),
            query_text=query,
            chunks=[
                {
                    "chunk_id": rc.chunk.id,
                    "chunk_text_preview": rc.chunk.text[:500],
                    "similarity_score": rc.score,
                    "retrieval_method": rc.retrieval_method.value
                        if hasattr(rc.retrieval_method, "value")
                        else str(rc.retrieval_method),
                    "document_version": rc.chunk.metadata.document_version,
                }
                for rc in chunks[:20]
            ],
        )
        await session.commit()
    except Exception as e:
        logger.warning("lineage_recording_failed", error=str(e))