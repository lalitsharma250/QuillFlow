"""
app/api/v1/chat.py

POST /v1/chat — Main query endpoint.

Two modes:
  1. Streaming (default): Returns SSE stream of events
  2. Non-streaming: Returns complete JSON response

The endpoint:
  1. Validates the request
  2. Authenticates the user
  3. Creates initial graph state
  4. Invokes the LangGraph DAG
  5. Returns the result (streamed or complete)
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
from app.db.repository import AuditRepository, LineageRepository
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

@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    _rate_limit: None = Depends(RateLimiter("chat")),
    auth: AuthContext = Depends(require_viewer),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Main query endpoint.

    Accepts a user query, runs it through the QuillFlow DAG,
    and returns the generated response.

    Supports streaming (SSE) and non-streaming modes.
    """
    # ── Validate graph is available ────────────────────
    compiled_graph = getattr(http_request.app.state, "compiled_graph", None)
    if compiled_graph is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready — graph not compiled",
        )

    response_id = str(uuid4())

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
            "model_preference": request.model_preference,
            "stream": request.stream,
        },
        ip_address=http_request.client.host if http_request.client else None,
    )
    await session.commit()

    # ── Create initial state ───────────────────────────
    initial_state = create_initial_state(
        query=request.query,
        auth=auth,
        conversation_id=request.conversation_id,
        model_preference=request.model_preference,
        include_sources=request.include_sources,
        max_sections=request.max_sections,
        stream=request.stream,
        response_id=response_id,
        history=[{"role": m.role, "content": m.content} for m in request.history],
    )

    if request.stream:
        return StreamingResponse(
            _stream_response(compiled_graph, initial_state, auth, response_id, session),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )
    else:
        return await _complete_response(
            compiled_graph, initial_state, auth, response_id, session, request
        )


async def _complete_response(
    graph,
    initial_state: dict,
    auth: AuthContext,
    response_id: str,
    session: AsyncSession,
    request: ChatRequest,
) -> ChatResponse:
    """
    Non-streaming mode: run graph to completion and return full response.
    """
    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.error("graph_execution_failed", error=str(e), response_id=response_id)
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)[:200]}")

    # ── Check for errors ───────────────────────────────
    error = final_state.get("error")
    if error and not final_state.get("final_output"):
        raise HTTPException(status_code=422, detail=error)

    # ── Handle cache hit ───────────────────────────────
    if final_state.get("cache_hit") and final_state.get("cached_response"):
        cached = final_state["cached_response"]
        return ChatResponse(
            response_id=UUID(response_id),
            content=cached.get("content", ""),
            query_type=cached.get("query_type", "simple"),
            sources=[
                SourceReference(**s) for s in cached.get("sources", [])
            ] if request.include_sources else [],
            usage=TokenUsage(),
            cached=True,
        )

    # ── Build response from final state ────────────────
    content = final_state.get("final_output", "")
    query_type = final_state.get("query_type", "simple")
    if hasattr(query_type, "value"):
        query_type = query_type.value

    chunks = final_state.get("retrieved_chunks", [])
    total_usage = final_state.get("total_usage") or TokenUsage()
    eval_scores = final_state.get("eval_scores")

    # Build sources
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

    # ── Record lineage ─────────────────────────────────
    await _record_lineage(session, response_id, request.query, chunks)

    # ── Build eval summary ─────────────────────────────
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
    )


async def _stream_response(
    graph,
    initial_state: dict,
    auth: AuthContext,
    response_id: str,
    session: AsyncSession,
):
    """
    Streaming mode: run graph and yield SSE events as nodes complete.

    Event sequence:
      1. stream_start (metadata)
      2. status_update (pipeline progress)
      3. content_delta / section_start / section_end (content)
      4. stream_end (summary with sources and usage)
    """
    # ── Stream start ───────────────────────────────────
    yield StreamEvent(
        type=StreamEventType.STREAM_START,
        response_id=UUID(response_id),
    ).to_sse()

    try:
        # Run graph with streaming state updates
        final_state = None

        async for state_update in graph.astream(initial_state):
            # astream yields {node_name: state_updates} dicts
            for node_name, node_output in state_update.items():
                if node_output is None:
                    continue

                # Merge into tracking state
                if final_state is None:
                    final_state = {**initial_state, **node_output}
                else:
                    final_state.update(node_output)

                # Emit events based on which node completed
                async for event in _node_to_events(node_name, node_output, final_state):
                    yield event

        if final_state is None:
            final_state = initial_state

        # ── Check for errors ───────────────────────────
        error = final_state.get("error")
        if error and not final_state.get("final_output"):
            yield StreamEvent(
                type=StreamEventType.ERROR,
                error_detail=error,
            ).to_sse()
            return

        # ── Stream end ─────────────────────────────────
        chunks = final_state.get("retrieved_chunks", [])
        total_usage = final_state.get("total_usage") or TokenUsage()

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

        yield StreamEvent(
            type=StreamEventType.STREAM_END,
            sources=sources,
            usage=total_usage,
        ).to_sse()

        # ── Record lineage (after stream completes) ────
        query = final_state.get("query", "")
        await _record_lineage(session, response_id, query, chunks)

    except Exception as e:
        logger.error(
            "stream_error",
            error=str(e),
            response_id=response_id,
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            error_detail=f"Generation failed: {str(e)[:200]}",
        ).to_sse()


async def _node_to_events(
    node_name: str,
    node_output: dict,
    full_state: dict,
):
    """
    Convert a node's output into SSE events.
    Each node type produces different events.
    """
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
            # Stream the cached content as a single delta
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
            # Simple query — stream the direct answer
            yield StreamEvent(
                type=StreamEventType.CONTENT_DELTA,
                content=final_output,
            ).to_sse()
        elif final_output and full_state.get("section_drafts"):
            # Complex query — stream the merged/polished version
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
