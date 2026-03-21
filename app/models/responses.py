"""
app/models/responses.py

Pydantic models for API responses.
These define the exact shape of data returned to clients.

Two response patterns:
  1. Standard JSON response (non-streaming)
  2. SSE stream of StreamEvent objects (streaming)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════
# Standard Responses
# ═══════════════════════════════════════════════════════════


class SourceReference(BaseModel):
    """A single source citation in the response."""

    filename: str
    page_number: int | None = None
    section_heading: str | None = None
    chunk_text_preview: str = Field(
        max_length=200,
        description="First ~200 chars of the chunk for context",
    )
    relevance_score: float = Field(ge=0.0, le=1.0)

class TokenUsage(BaseModel):
    """Token consumption for a single request — used for cost tracking."""

    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    total_tokens: int = Field(ge=0, default=0)
    estimated_cost_usd: float = Field(ge=0.0, default=0.0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Accumulate usage across multiple LLM calls in a single request."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            estimated_cost_usd=self.estimated_cost_usd + other.estimated_cost_usd,
        )


class EvalScoreSummary(BaseModel):
    """Subset of eval scores exposed to the client (not all internal metrics)."""

    faithfulness: float | None = None
    relevancy: float | None = None

class ChatResponse(BaseModel):
    """
    Response body for POST /v1/chat (non-streaming mode).

    Example:
    {
        "response_id": "...",
        "content": "The transformer architecture revolutionized...",
        "query_type": "complex",
        "sources": [...],
        "usage": {"input_tokens": 1500, "output_tokens": 800},
        "created_at": "2025-01-15T10:30:00Z"
    }
    """

    response_id: UUID
    content: str
    query_type: str  # "simple" or "complex"
    sources: list[SourceReference] = Field(default_factory=list)
    usage: TokenUsage
    eval_scores: EvalScoreSummary | None = None
    cached: bool = Field(default=False, description="Whether this was served from cache")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



# ── Fix forward reference: ChatResponse uses TokenUsage ──
# Pydantic v2 handles this automatically with model_rebuild
ChatResponse.model_rebuild()


# ═══════════════════════════════════════════════════════════
# Streaming (SSE) Events
# ═══════════════════════════════════════════════════════════


class StreamEventType(str, Enum):
    """
    Types of events sent over the SSE stream.
    Client uses this to know how to handle each event.
    """

    # ── Lifecycle events ───────────────────────
    STREAM_START = "stream_start"      # First event — includes metadata
    STREAM_END = "stream_end"          # Last event — includes final summary

    # ── Content events ─────────────────────────
    CONTENT_DELTA = "content_delta"    # Incremental text chunk
    SECTION_START = "section_start"    # New section beginning (complex queries)
    SECTION_END = "section_end"        # Section complete

    # ── Status events ──────────────────────────
    STATUS_UPDATE = "status_update"    # Pipeline progress (e.g. "retrieving context...")
    ERROR = "error"                    # Something went wrong


class StreamEvent(BaseModel):
    """
    A single event in the SSE stream.

    Wire format (Server-Sent Events):
      event: content_delta
      data: {"type": "content_delta", "content": "The transformer..."}

    Example stream for a complex query:
      1. stream_start   → {"response_id": "...", "query_type": "complex"}
      2. status_update   → {"message": "Retrieving relevant context..."}
      3. status_update   → {"message": "Planning content structure..."}
      4. section_start   → {"heading": "Introduction"}
      5. content_delta   → {"content": "The transformer architecture"}
      6. content_delta   → {"content": " was introduced in 2017..."}
      7. section_end     → {"heading": "Introduction", "word_count": 150}
      8. section_start   → {"heading": "Key Innovations"}
      9. content_delta   → {"content": "Self-attention mechanisms..."}
      ...
      N. stream_end      → {"sources": [...], "usage": {...}}
    """

    type: StreamEventType
    content: str | None = Field(
        default=None,
        description="Text content (for content_delta events)",
    )
    heading: str | None = Field(
        default=None,
        description="Section heading (for section_start/end events)",
    )
    message: str | None = Field(
        default=None,
        description="Status message (for status_update events)",
    )
    response_id: UUID | None = Field(
        default=None,
        description="Response ID (sent in stream_start)",
    )

    query_type: str | None = Field(
        default=None,
        description="Query classification (sent in stream_start)",
    )
    word_count: int | None = Field(
        default=None,
        description="Word count (sent in section_end)",
    )
    sources: list[SourceReference] | None = Field(
        default=None,
        description="Source citations (sent in stream_end)",
    )
    usage: TokenUsage | None = Field(
        default=None,
        description="Token usage summary (sent in stream_end)",
    )
    error_detail: str | None = Field(
        default=None,
        description="Error description (sent in error events)",
    )

    def to_sse(self) -> str:
        """
        Serialize this event to SSE wire format.

        Returns:
            String like:
            event: content_delta
            data: {"type":"content_delta","content":"hello"}
        """
        data = self.model_dump_json(exclude_none=True)
        return f"event: {self.type.value}\ndata: {data}\n\n"


# ═══════════════════════════════════════════════════════════
# Ingestion Responses
# ═══════════════════════════════════════════════════════════


class IngestResponse(BaseModel):
    """
    Response body for POST /v1/ingest

    Example:
    {
        "document_id": "...",
        "filename": "research_paper.pdf",
        "status": "processing",
        "message": "Document accepted. Chunking and indexing in progress.",
        "chunk_count": null
    }
    """

    document_id: UUID
    filename: str
    status: str  # "processing", "indexed", "failed"
    message: str
    chunk_count: int | None = Field(
        default=None,
        description="Number of chunks created (populated when status='indexed')",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocumentResponse(BaseModel):
    """
    Response body for GET /v1/documents and GET /v1/documents/{id}

    Example:
    {
        "document_id": "...",
        "filename": "research_paper.pdf",
        "content_type": "pdf",
        "status": "indexed",
        "chunk_count": 47,
        "version": 1,
        "created_at": "2025-01-15T10:30:00Z"
    }
    """

    document_id: UUID
    filename: str
    content_type: str
    status: str
    error_message: str | None = None
    chunk_count: int | None = None
    version: int
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    documents: list[DocumentResponse]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


# ═══════════════════════════════════════════════════════════
# Health & System Responses
# ═══════════════════════════════════════════════════════════

class ComponentHealth(BaseModel):
    """Health status of a single component (Qdrant, Redis, Postgres)."""

    status: str  # "healthy", "degraded", "unhealthy"
    latency_ms: float | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    """Response body for GET /v1/health"""

    status: str = "healthy"
    service: str = "quillflow"
    version: str
    checks: dict[str, ComponentHealth] = Field(default_factory=dict)

# ── Rebuild models with forward references ────────────
HealthResponse.model_rebuild()

class BulkIngestResponse(BaseModel):
    """
    Response body for POST /v1/ingest/bulk

    Returns immediately with a job ID. Client polls for progress.

    Example:
    {
        "job_id": "...",
        "status": "accepted",
        "total_documents": 50,
        "message": "Bulk ingestion job accepted. Poll GET /v1/ingest/jobs/{job_id} for progress."
    }
    """

    job_id: UUID
    status: str = "accepted"
    total_documents: int
    document_ids: list[UUID]
    message: str = Field(
        default="Bulk ingestion job accepted. Poll GET /v1/ingest/jobs/{job_id} for progress."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class JobDocumentStatus(BaseModel):
    """Status of a single document within a bulk job."""

    document_id: UUID
    filename: str
    status: str  # "pending", "processing", "indexed", "failed"
    error_message: str | None = None
    chunk_count: int | None = None
    
class JobStatusResponse(BaseModel):
    """
    Response body for GET /v1/ingest/jobs/{job_id}

    Example:
    {
        "job_id": "...",
        "status": "processing",
        "total_documents": 50,
        "processed_documents": 23,
        "failed_documents": 1,
        "progress_percent": 46.0,
        "documents": [
            {"document_id": "...", "filename": "doc1.txt", "status": "indexed"},
            {"document_id": "...", "filename": "doc2.txt", "status": "processing"},
            {"document_id": "...", "filename": "doc3.txt", "status": "failed", "error": "..."}
        ]
    }
    """

    job_id: UUID
    status: str
    total_documents: int
    processed_documents: int
    failed_documents: int
    progress_percent: float = Field(ge=0.0, le=100.0)
    documents: list[JobDocumentStatus]
    created_at: datetime
    completed_at: datetime | None = None


# Rebuild for forward references
JobStatusResponse.model_rebuild()