"""
app/models/domain.py

Core domain types for QuillFlow.

Rules:
  1. These are pure DATA models — no business logic, no I/O.
  2. Every field has a type annotation and sensible default where appropriate.
  3. Validators enforce structural constraints (not business rules).
  4. These models are used across all layers (services, graph, API).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import MAX_PLAN_SECTIONS, MAX_SECTION_WORDS, MIN_SECTION_WORDS


# ═══════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════


class QueryType(str, Enum):
    """
    Router node classifies every incoming query into one of these.
    This determines the DAG execution path.
    """

    SIMPLE = "simple"    # Direct answer — skip planner/writers, single LLM call
    COMPLEX = "complex"  # Needs planning → parallel writing → reduction


class DocumentStatus(str, Enum):
    """Tracks a document through the ingestion pipeline."""

    PENDING = "pending"        # Uploaded, not yet processed
    PROCESSING = "processing"  # Currently being parsed/chunked/embedded
    INDEXED = "indexed"        # Successfully stored in vector DB
    FAILED = "failed"          # Ingestion failed (see error field)
    SUPERSEDED = "superseded"

class RetrievalMethod(str, Enum):
    """How a chunk was retrieved — useful for debugging and metrics."""

    DENSE = "dense"    # Vector similarity search
    SPARSE = "sparse"  # BM25 / keyword search
    HYBRID = "hybrid"  # Combined dense + sparse

class JobStatus(str, Enum):
    """Tracks a bulk ingestion job through its lifecycle."""

    ACCEPTED = "accepted"        # Job created, not yet started
    PROCESSING = "processing"    # Actively processing documents
    COMPLETED = "completed"      # All documents processed (some may have failed)
    FAILED = "failed"            # Job-level failure (e.g. infra issue)


# ═══════════════════════════════════════════════════════════
# Documents & Chunks
# ═══════════════════════════════════════════════════════════


class ChunkMetadata(BaseModel):
    """
    Metadata attached to every chunk.
    Used for:
      - Filtering in Qdrant (e.g. "only chunks from document X")
      - Lineage tracking (which doc version produced this chunk)
      - Citation generation (page number, section heading)
    """

    org_id: UUID
    source_doc_id: UUID
    source_filename: str
    page_number: int | None = None
    section_heading: str | None = None
    chunk_index: int = Field(ge=0, description="Position of this chunk within the document")
    total_chunks: int | None = None
    document_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}  # Metadata is immutable once created


class Chunk(BaseModel):
    """
    A single chunk of text ready for embedding and storage.

    Lifecycle:
      1. Created by chunker (text + metadata, no embedding)
      2. Embedding added by embedder
      3. Stored in Qdrant with all fields
    """

    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=10_000)
    metadata: ChunkMetadata
    embedding: list[float] | None = None  # None until embedding step

    @property
    def has_embedding(self) -> bool:
        return self.embedding is not None and len(self.embedding) > 0


class Document(BaseModel):
    """
    A source document as received from the user.
    This is the pre-chunking representation.
    """

    id: UUID = Field(default_factory=uuid4)
    org_id: UUID
    filename: str = Field(min_length=1, max_length=500)
    content_type: str = Field(
        description="Document type: 'pdf', 'html', 'text', 'markdown'"
    )
    raw_text: str = Field(default="", description="Extracted text content")
    status: DocumentStatus = DocumentStatus.PENDING
    error_message: str | None = None  # Populated if status == FAILED
    version: int = Field(default=1, ge=1)
    chunk_count: int | None = None  # Populated after chunking
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        allowed = {"pdf", "html", "text", "markdown"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"content_type must be one of {allowed}, got '{v}'")
        return v_lower


# ═══════════════════════════════════════════════════════════
# Content Planning
# ═══════════════════════════════════════════════════════════


class SectionPlan(BaseModel):
    """
    One section in the content plan generated by the Planner node.
    The Writer node receives this as its assignment.
    """

    heading: str = Field(min_length=1, max_length=200)
    description: str = Field(
        min_length=1,
        max_length=1000,
        description="What this section should cover — instructions for the writer",
    )
    word_budget: int = Field(
        ge=MIN_SECTION_WORDS,
        le=MAX_SECTION_WORDS,
        description="Target word count for this section",
    )
    key_points: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Specific points the writer must address",
    )


class ContentPlan(BaseModel):
    """
    Full content plan generated by the Planner node.
    Consumed by the Writer nodes (one writer per section).
    """

    title: str = Field(min_length=1, max_length=300)
    sections: list[SectionPlan] = Field(min_length=1, max_length=MAX_PLAN_SECTIONS)
    total_word_budget: int = Field(ge=100, le=10_000)
    target_audience: str = Field(
        default="general",
        description="Who this content is for — affects tone and complexity",
    )

    @field_validator("sections")
    @classmethod
    def validate_section_budgets(cls, sections: list[SectionPlan]) -> list[SectionPlan]:
        """Ensure individual section budgets don't wildly exceed total."""
        total = sum(s.word_budget for s in sections)
        # Allow 20% overflow (reducer can trim)
        if total > len(sections) * MAX_SECTION_WORDS * 1.2:
            raise ValueError(
                f"Combined section word budgets ({total}) exceed maximum ({len(sections) * MAX_SECTION_WORDS * 1.2})"
            )
        return sections


# ═══════════════════════════════════════════════════════════
# Retrieval Results
# ═══════════════════════════════════════════════════════════


class RetrievedChunk(BaseModel):
    """
    A chunk returned from the retrieval pipeline, enriched with relevance info.
    This is what the Planner and Writer nodes see as "context".
    """

    chunk: Chunk
    score: float = Field(ge=0.0, le=1.0, description="Relevance score (higher = better)")
    retrieval_method: RetrievalMethod = RetrievalMethod.DENSE

    @property
    def text(self) -> str:
        """Convenience accessor — most consumers just need the text."""
        return self.chunk.text

    @property
    def source(self) -> str:
        """Human-readable source reference for citations."""
        meta = self.chunk.metadata
        parts = [meta.source_filename]
        if meta.page_number is not None:
            parts.append(f"p.{meta.page_number}")
        if meta.section_heading:
            parts.append(meta.section_heading)
        return " › ".join(parts)


# ═══════════════════════════════════════════════════════════
# Writer Output
# ═══════════════════════════════════════════════════════════


class SectionDraft(BaseModel):
    heading: str
    content: str = Field(min_length=1)
    word_count: int = Field(ge=0, default=0)
    sources_used: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def compute_word_count_if_missing(self) -> SectionDraft:
        """Auto-compute word count from content if not provided."""
        if self.word_count == 0 and self.content:
            self.word_count = len(self.content.split())
        return self


# ═══════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════


class EvalScores(BaseModel):
    """
    RAG quality scores for a single response.
    Computed by the Validator node and logged for monitoring.

    All scores are 0.0 to 1.0 (higher = better).
    None means "not computed" (e.g. skipped for simple queries).
    """

    faithfulness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Is the answer grounded in the retrieved context?",
    )
    answer_relevancy: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Does the answer actually address the query?",
    )
    context_precision: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Are the retrieved chunks relevant to the query?",
    )
    context_recall: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Did we retrieve all the chunks needed to answer?",
    )

    @property
    def is_acceptable(self) -> bool:
        """Quick check: are all computed scores above minimum thresholds?"""
        from config import get_settings

        settings = get_settings()
        checks = []
        if self.faithfulness is not None:
            checks.append(self.faithfulness >= settings.eval_faithfulness_threshold)
        if self.answer_relevancy is not None:
            checks.append(self.answer_relevancy >= settings.eval_relevancy_threshold)
        if self.context_precision is not None:
            checks.append(
                self.context_precision >= settings.eval_context_precision_threshold
            )
        # If nothing was computed, we can't reject
        return all(checks) if checks else True

class IngestionJob(BaseModel):
    """
    Tracks a bulk ingestion job.
    One job contains N documents, each processed independently.
    """

    id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.ACCEPTED
    total_documents: int = Field(ge=1)
    processed_documents: int = Field(ge=0, default=0)
    failed_documents: int = Field(ge=0, default=0)
    document_ids: list[UUID] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class AuthContext(BaseModel):
    """
    Authenticated user context.
    Created by auth middleware, passed to every service call.
    This is how we know WHO is making the request and WHAT they can do.
    """

    user_id: UUID
    org_id: UUID
    email: str
    role: str  # "admin", "editor", "viewer"
    is_superadmin: bool = False

    def can_ingest(self) -> bool:
        """Can this user upload/ingest documents?"""
        return self.role in ("admin", "editor")

    def can_query(self) -> bool:
        """Can this user make chat queries?"""
        return True  # All roles can query

    def can_manage_users(self) -> bool:
        """Can this user create/deactivate other users?"""
        return self.role == "admin"

    def can_view_all_documents(self) -> bool:
        """Can this user see all org documents (not just their own)?"""
        return self.role == "admin"