"""
app/db/models.py

SQLAlchemy ORM models for QuillFlow.

These define the Postgres schema. Each class maps to one table.

Naming convention:
  - ORM models are suffixed with 'Record' to distinguish from
    Pydantic domain models (e.g. DocumentRecord vs Document)

Tables:
  - documents:        Source documents and their ingestion status
  - ingestion_jobs:   Bulk ingestion job tracking
  - job_documents:    Many-to-many: which documents belong to which job
  - response_lineage: Tracks which chunks were used to generate each response
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text, 
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


# ═══════════════════════════════════════════════════════════
# Documents
# ═══════════════════════════════════════════════════════════


class DocumentRecord(Base):
    """
    A source document tracked through the ingestion pipeline.

    Lifecycle:
      pending → processing → indexed (success)
                           → failed  (error)
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        comment="Organization that owns this document",
    )
    organization: Mapped[OrganizationRecord] = relationship()
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    job_links: Mapped[list[JobDocumentLink]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_filename", "filename"),
        Index("ix_documents_org_id", "org_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentRecord(id={self.id}, filename='{self.filename}', "
            f"status='{self.status}')>"
        )


# ═══════════════════════════════════════════════════════════
# Ingestion Jobs
# ═══════════════════════════════════════════════════════════


class IngestionJobRecord(Base):
    """
    A bulk ingestion job containing multiple documents.

    Lifecycle:
      accepted → processing → completed (all done, some may have failed)
                             → failed    (job-level failure)
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        comment="Organization that owns this job",
    )
    organization: Mapped[OrganizationRecord] = relationship()
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="accepted"
    )
    total_documents: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_documents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    failed_documents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    document_links: Mapped[list[JobDocumentLink]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_ingestion_jobs_status", "status"),
        Index("ix_ingestion_jobs_created_at", "created_at"),
        Index("ix_ingestion_jobs_org_id", "org_id"),
    )

    @property
    def progress_percent(self) -> float:
        if self.total_documents == 0:
            return 0.0
        done = self.processed_documents + self.failed_documents
        return round((done / self.total_documents) * 100, 1)

    def __repr__(self) -> str:
        return (
            f"<IngestionJobRecord(id={self.id}, status='{self.status}', "
            f"progress={self.progress_percent}%)>"
        )


# ═══════════════════════════════════════════════════════════
# Job ↔ Document Link (Many-to-Many)
# ═══════════════════════════════════════════════════════════


class JobDocumentLink(Base):
    """
    Links documents to their ingestion job.

    Why a separate table instead of a foreign key on documents?
      - A document can be re-ingested in a new job (new version)
      - A job contains many documents
      - We need to track per-document status within a job context
    """

    __tablename__ = "job_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    # Relationships
    job: Mapped[IngestionJobRecord] = relationship(back_populates="document_links")
    document: Mapped[DocumentRecord] = relationship(back_populates="job_links")

    __table_args__ = (
        Index("ix_job_documents_job_id", "job_id"),
        Index("ix_job_documents_document_id", "document_id"),
    )


# ═══════════════════════════════════════════════════════════
# Response Lineage
# ═══════════════════════════════════════════════════════════


class ResponseLineageRecord(Base):
    """
    Tracks which chunks were used to generate a specific response.

    Purpose:
      - Audit trail: "Why did the system say X?"
      - Debugging: "Which chunks influenced this answer?"
      - Evaluation: Compare retrieved chunks vs ground truth

```python
    Example:
      Response R1 used chunks [C1, C2, C5] with scores [0.95, 0.88, 0.72]
      → 3 rows in this table, all sharing the same response_id
    """

    __tablename__ = "response_lineage"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    response_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, index=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False,
        comment="References the chunk ID stored in Qdrant (not a FK — different store)",
    )
    chunk_text_preview: Mapped[str] = mapped_column(
        String(500), nullable=False, default="",
        comment="First ~500 chars of chunk text for quick inspection",
    )
    similarity_score: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Relevance score at retrieval time",
    )
    retrieval_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="dense",
        comment="How this chunk was retrieved: dense, sparse, hybrid",
    )
    document_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="Version of the source document when this chunk was created",
    )
    query_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="The original query that triggered this retrieval",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_response_lineage_response_id", "response_id"),
        Index("ix_response_lineage_chunk_id", "chunk_id"),
        Index("ix_response_lineage_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ResponseLineageRecord(response_id={self.response_id}, "
            f"chunk_id={self.chunk_id}, score={self.similarity_score})>"
        )

# ═══════════════════════════════════════════════════════════
# Organizations & Users
# ═══════════════════════════════════════════════════════════


class OrganizationRecord(Base):
    """
    A tenant/organization. All data is scoped to an org.
    This is the top-level isolation boundary.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Org-level config: allowed models, token budgets, etc.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    users: Mapped[list[UserRecord]] = relationship(back_populates="organization")
    api_keys: Mapped[list[ApiKeyRecord]] = relationship(back_populates="organization")

    def __repr__(self) -> str:
        return f"<OrganizationRecord(id={self.id}, name='{self.name}')>"


class UserRole(str, enum.Enum):
    """Roles determine what actions a user can perform."""

    ADMIN = "admin"      # Full access: ingest, query, manage users, view all
    EDITOR = "editor"    # Can ingest + query, view own docs
    VIEWER = "viewer"    # Query only, view own responses


class UserRecord(Base):
    """
    A user within an organization.
    Users authenticate via API keys (not passwords — this is an API, not a web app).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    organization: Mapped[OrganizationRecord] = relationship(
        back_populates="users",
        lazy="selectin",
    )
    api_keys: Mapped[list[ApiKeyRecord]] = relationship(back_populates="user")

    __table_args__ = (
        Index("ix_users_org_id", "org_id"),
        Index("ix_users_email", "email"),
    )

    def __repr__(self) -> str:
        return f"<UserRecord(id={self.id}, email='{self.email}', role='{self.role}')>"


class ApiKeyRecord(Base):
    """
    API keys for authentication.
    Each key is tied to a user and org.

    Security:
      - We store a SHA-256 hash of the key, never the raw key.
      - The raw key is shown ONCE at creation time.
      - Keys can be rotated (deactivate old, create new).
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="SHA-256 hash of the API key",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(12), nullable=False,
        comment="First 8 chars of key for identification (e.g. 'qf_live_ab')",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default",
        comment="Human-readable label for this key",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Optional expiry. Null = never expires.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped[UserRecord] = relationship(lazy="selectin")
    organization: Mapped[OrganizationRecord] = relationship(back_populates="api_keys")

    __table_args__ = (
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_org_id", "org_id"),
    )


class AuditLogRecord(Base):
    """
    Immutable audit trail for all significant actions.
    Write-only — never updated or deleted.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Action type: 'query', 'ingest', 'bulk_ingest', 'api_key_created', etc.",
    )
    resource_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="What was acted on: 'document', 'job', 'chat', etc.",
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True,
        comment="ID of the resource acted on",
    )
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Additional context (sanitized — no PII or secrets)",
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_org_id", "org_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

class InviteCodeRecord(Base):
    __tablename__ = "invite_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    organization: Mapped["OrganizationRecord"] = relationship(lazy="selectin")

    __table_args__ = (
        Index("ix_invite_codes_code", "code"),
        Index("ix_invite_codes_org_id", "org_id"),
    )

    @property
    def is_valid(self) -> bool:
        """Check if code is still usable."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return (
            self.is_active
            and self.times_used < self.max_uses
            and self.expires_at > now
        )

    def __repr__(self) -> str:
        return f"<InviteCode(code='{self.code}', org={self.org_id}, uses={self.times_used}/{self.max_uses})>"