"""
app/db/repository.py

Repository classes for database CRUD operations.

Rules:
  1. All DB access goes through repositories — never raw queries in services/API.
  2. Repositories accept and return Pydantic domain models (not ORM objects).
  3. The caller manages the session lifecycle (commit/rollback via dependency).
  4. Repositories are stateless — they receive a session, use it, done.

Conversion pattern:
  ORM Record → Pydantic Model  (for reads, returning to caller)
  Pydantic Model → ORM Record  (for writes, persisting to DB)
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    DocumentRecord,
    IngestionJobRecord,
    JobDocumentLink,
    ResponseLineageRecord,
    AuditLogRecord,
)
from app.models.domain import (
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    RetrievalMethod,
)


# ═══════════════════════════════════════════════════════════
# Document Repository
# ═══════════════════════════════════════════════════════════


class DocumentRepository:
    """CRUD operations for source documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Conversions ────────────────────────────────────

    @staticmethod
    def _to_domain(record: DocumentRecord) -> Document:
        """Convert ORM record to Pydantic domain model."""
        return Document(
            id=record.id,
            org_id=record.org_id,
            filename=record.filename,
            content_type=record.content_type,
            raw_text=record.raw_text,
            status=DocumentStatus(record.status),
            error_message=record.error_message,
            version=record.version,
            chunk_count=record.chunk_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    # ── Create ─────────────────────────────────────────

    async def create_document(
        self,
        org_id: UUID,
        filename: str,
        content_type: str,
        raw_text: str,
        metadata: dict[str, str] | None = None,
    ) -> Document:
        """
        Create a new document record with status='pending'.
        Returns the Pydantic domain model.
        """
        record = DocumentRecord(
            id=uuid4(),
            org_id=org_id,
            filename=filename,
            content_type=content_type,
            raw_text=raw_text,
            status="pending",
            metadata_=metadata or {},
        )
        self._session.add(record)
        await self._session.flush()  # Populate defaults without committing
        return self._to_domain(record)

    async def create_documents_batch(
        self,
        org_id: UUID,
        documents: list[dict],
    ) -> list[Document]:
        """
        Create multiple document records in a single batch.
        Each dict should have: filename, content_type, raw_text, metadata.
        Returns list of Pydantic domain models.
        """
        records = []
        for doc_data in documents:
            record = DocumentRecord(
                id=uuid4(),
                org_id=org_id,  
                filename=doc_data["filename"],
                content_type=doc_data["content_type"],
                raw_text=doc_data["raw_text"],
                status="pending",
                metadata_=doc_data.get("metadata", {}),
            )
            records.append(record)

        self._session.add_all(records)
        await self._session.flush()
        return [self._to_domain(r) for r in records]

    # ── Read ───────────────────────────────────────────

    async def get_document(self, document_id: UUID, org_id: UUID) -> Document | None:
        stmt = select(DocumentRecord).where(
            DocumentRecord.id == document_id,
            DocumentRecord.org_id == org_id,  # ← ISOLATION
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def get_document_internal(self, document_id: UUID) -> Document | None:
        """For worker use — no org scoping."""
        stmt = select(DocumentRecord).where(DocumentRecord.id == document_id)
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def list_documents(
        self,
        org_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        include_superseded: bool = False,
    ) -> tuple[list[Document], int]:
        """List documents for an org, excluding superseded by default."""
        
        # Base query
        base_filter = [DocumentRecord.org_id == org_id]
        
        if status:
            base_filter.append(DocumentRecord.status == status)
        elif not include_superseded:
            # Exclude superseded unless specifically filtered
            base_filter.append(DocumentRecord.status != "superseded")

        # Count
        count_stmt = select(func.count(DocumentRecord.id)).where(*base_filter)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one()

        # Fetch page
        stmt = (
            select(DocumentRecord)
            .where(*base_filter)
            .order_by(DocumentRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [self._to_domain(r) for r in records], total

    # ── Update ─────────────────────────────────────────

    async def update_document_status(
        self,
        document_id: UUID,
        status: str,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update a document's status and optional fields."""
        values: dict = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if error_message is not None:
            values["error_message"] = error_message
        if chunk_count is not None:
            values["chunk_count"] = chunk_count

        stmt = (
            update(DocumentRecord)
            .where(DocumentRecord.id == document_id)
            .values(**values)
        )
        await self._session.execute(stmt)

    async def increment_document_version(self, document_id: UUID) -> int:
        """
        Increment document version (for re-ingestion).
        Returns the new version number.
        """
        stmt = (
            update(DocumentRecord)
            .where(DocumentRecord.id == document_id)
            .values(
                version=DocumentRecord.version + 1,
                status="pending",
                error_message=None,
                chunk_count=None,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(DocumentRecord.version)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
    
    async def find_existing_document(
        self,
        org_id: UUID,
        filename: str,
    ) -> Document | None:
        """Find an existing document by filename within an org."""
        stmt = (
            select(DocumentRecord)
            .where(
                DocumentRecord.org_id == org_id,
                DocumentRecord.filename == filename,
                DocumentRecord.status != "failed",
            )
            .order_by(DocumentRecord.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def create_new_version(
        self,
        org_id: UUID,
        filename: str,
        content_type: str,
        raw_text: str,
        metadata: dict | None = None,
        previous_version: int = 0,
    ) -> Document:
        """Create a new version of an existing document."""
        new_version = previous_version + 1

        record = DocumentRecord(
            id=uuid4(),
            org_id=org_id,
            filename=filename,
            content_type=content_type,
            raw_text=raw_text,
            status="pending",
            version=new_version,
            metadata_=metadata or {},
        )
        self._session.add(record)
        await self._session.flush()

        return self._to_domain(record)

    async def deactivate_old_versions(
        self,
        org_id: UUID,
        filename: str,
        keep_version: int,
    ) -> int:
        """
        Mark old versions as superseded.
        Returns count of deactivated versions.
        """
        stmt = (
            update(DocumentRecord)
            .where(
                DocumentRecord.org_id == org_id,
                DocumentRecord.filename == filename,
                DocumentRecord.version < keep_version,
                DocumentRecord.status == "indexed",
            )
            .values(status="superseded")
        )
        result = await self._session.execute(stmt)
        return result.rowcount


# ═══════════════════════════════════════════════════════════
# Ingestion Job Repository
# ═══════════════════════════════════════════════════════════


class IngestionJobRepository:
    """CRUD operations for bulk ingestion jobs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Conversions ────────────────────────────────────

    @staticmethod
    def _to_domain(record: IngestionJobRecord) -> IngestionJob:
        """Convert ORM record to Pydantic domain model."""
        document_ids = [link.document_id for link in record.document_links]
        return IngestionJob(
            id=record.id,
            status=JobStatus(record.status),
            total_documents=record.total_documents,
            processed_documents=record.processed_documents,
            failed_documents=record.failed_documents,
            document_ids=document_ids,
            error_message=record.error_message,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )

    # ── Create ─────────────────────────────────────────

    async def create_job(
        self,
        org_id: UUID,
        document_ids: list[UUID],
    ) -> IngestionJob:
        """
        Create a new ingestion job linked to the given documents.
        Returns the Pydantic domain model.
        """
        job_id = uuid4()

        # Create job record
        job_record = IngestionJobRecord(
            id=job_id,
            org_id=org_id,
            status="accepted",
            total_documents=len(document_ids),
        )
        self._session.add(job_record)

        # Create link records
        for doc_id in document_ids:
            link = JobDocumentLink(
                id=uuid4(),
                job_id=job_id,
                document_id=doc_id,
            )
            self._session.add(link)

        await self._session.flush()

        # Reload with relationships
        return IngestionJob(
            id=job_id,
            status=JobStatus.ACCEPTED,
            total_documents=len(document_ids),
            processed_documents=0,
            failed_documents=0,
            document_ids=document_ids,
            created_at=job_record.created_at,
        )

    # ── Read ───────────────────────────────────────────

    # ── For API (org-scoped) ───────────────────────────
    async def get_job(self, job_id: UUID, org_id: UUID) -> IngestionJob | None:
        stmt = (
            select(IngestionJobRecord)
            .options(selectinload(IngestionJobRecord.document_links))
            .where(
                IngestionJobRecord.id == job_id,
                IngestionJobRecord.org_id == org_id,
            )
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    # ── For Worker (internal, no org scoping needed) ───
    async def get_job_internal(self, job_id: UUID) -> IngestionJob | None:
        """
        Fetch job without org scoping. 
        ONLY for use by background workers that already validated the job.
        """
        stmt = (
            select(IngestionJobRecord)
            .options(selectinload(IngestionJobRecord.document_links))
            .where(IngestionJobRecord.id == job_id)
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return self._to_domain(record) if record else None

    async def get_job_document_ids(self, job_id: UUID) -> list[UUID]:
        """Get all document IDs associated with a job."""
        stmt = (
            select(JobDocumentLink.document_id)
            .where(JobDocumentLink.job_id == job_id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_job_with_document_statuses(
        self, job_id: UUID, org_id: UUID
    ) -> tuple[IngestionJob | None, list[dict]]:
        """
        Fetch a job along with the status of each document in it.
        Returns (job, document_statuses) where document_statuses is a list of dicts.
        Used by the GET /v1/ingest/jobs/{id} endpoint.
        """
        # Get the job
        job = await self.get_job(job_id, org_id)
        if job is None:
            return None, []

        # Get document statuses
        stmt = (
            select(
                DocumentRecord.id,
                DocumentRecord.filename,
                DocumentRecord.status,
                DocumentRecord.error_message,
                DocumentRecord.chunk_count,
            )
            .join(JobDocumentLink, JobDocumentLink.document_id == DocumentRecord.id)
            .where(JobDocumentLink.job_id == job_id)
            .order_by(DocumentRecord.filename)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        doc_statuses = [
            {
                "document_id": row.id,
                "filename": row.filename,
                "status": row.status,
                "error_message": row.error_message,
                "chunk_count": row.chunk_count,
            }
            for row in rows
        ]

        return job, doc_statuses

    # ── Update ─────────────────────────────────────────

    async def update_job_status(self, job_id: UUID, status: str) -> None:
        """Update a job's status."""
        stmt = (
            update(IngestionJobRecord)
            .where(IngestionJobRecord.id == job_id)
            .values(status=status)
        )
        await self._session.execute(stmt)

    async def update_job_progress(
        self,
        job_id: UUID,
        processed_documents: int,
        failed_documents: int,
    ) -> None:
        """Update a job's progress counters."""
        stmt = (
            update(IngestionJobRecord)
            .where(IngestionJobRecord.id == job_id)
            .values(
                processed_documents=processed_documents,
                failed_documents=failed_documents,
            )
        )
        await self._session.execute(stmt)

    async def finalize_job(self, job_id: UUID, status: str) -> None:
        """Mark a job as completed/failed with a timestamp."""
        stmt = (
            update(IngestionJobRecord)
            .where(IngestionJobRecord.id == job_id)
            .values(
                status=status,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self._session.execute(stmt)


# ═══════════════════════════════════════════════════════════
# Response Lineage Repository
# ═══════════════════════════════════════════════════════════


class LineageRepository:
    """CRUD operations for response lineage tracking."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_lineage(
        self,
        response_id: UUID,
        query_text: str,
        chunks: list[dict],
    ) -> None:
        """
        Record which chunks were used to generate a response.

        Args:
            response_id: UUID of the generated response
            query_text: The original user query
            chunks: List of dicts with keys:
                chunk_id, chunk_text_preview, similarity_score,
                retrieval_method, document_version
        """
        records = []
        for chunk_data in chunks:
            record = ResponseLineageRecord(
                id=uuid4(),
                response_id=response_id,
                chunk_id=chunk_data["chunk_id"],
                chunk_text_preview=chunk_data.get("chunk_text_preview", "")[:500],
                similarity_score=chunk_data["similarity_score"],
                retrieval_method=chunk_data.get("retrieval_method", "dense"),
                document_version=chunk_data.get("document_version", 1),
                query_text=query_text,
            )
            records.append(record)

        self._session.add_all(records)
        await self._session.flush()

    async def get_response_lineage(
        self, response_id: UUID
    ) -> list[dict]:
        """
        Fetch all chunks used for a specific response.
        Returns list of dicts for easy serialization.
        """
        stmt = (
            select(ResponseLineageRecord)
            .where(ResponseLineageRecord.response_id == response_id)
            .order_by(ResponseLineageRecord.similarity_score.desc())
        )
        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "chunk_id": r.chunk_id,
                "chunk_text_preview": r.chunk_text_preview,
                "similarity_score": r.similarity_score,
                "retrieval_method": r.retrieval_method,
                "document_version": r.document_version,
                "query_text": r.query_text,
                "created_at": r.created_at,
            }
            for r in records
        ]

    async def get_chunk_usage_count(self, chunk_id: UUID) -> int:
        """How many responses have used a specific chunk. Useful for analytics."""
        stmt = (
            select(func.count(ResponseLineageRecord.id))
            .where(ResponseLineageRecord.chunk_id == chunk_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()


# ═══════════════════════════════════════════════════════════
# Audit Log Repository
# ═══════════════════════════════════════════════════════════

class AuditRepository:
    """Write-only audit log. Never update or delete."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        action: str,
        user_id: UUID | None = None,
        org_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        """
        Record an audit event.

        Actions:
          - "auth_success", "auth_failure"
          - "query", "query_cached"
          - "ingest_single", "ingest_bulk"

```python
          - "document_indexed", "document_failed"
          - "api_key_created", "api_key_deactivated"
          - "user_created", "user_deactivated"
          - "export_data" (GDPR)
        """
        record = AuditLogRecord(
            user_id=user_id,
            org_id=org_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
            ip_address=ip_address,
        )
        self._session.add(record)
        await self._session.flush()

    async def get_user_activity(
        self,
        user_id: UUID,
        limit: int = 50,
    ) -> list[dict]:
        """Fetch recent activity for a user (admin dashboard)."""
        stmt = (
            select(AuditLogRecord)
            .where(AuditLogRecord.user_id == user_id)
            .order_by(AuditLogRecord.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "created_at": r.created_at,
            }
            for r in records
        ]

    async def get_org_activity(
        self,
        org_id: UUID,
        action_filter: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch recent activity for an entire org (admin dashboard)."""
        stmt = (
            select(AuditLogRecord)
            .where(AuditLogRecord.org_id == org_id)
        )
        if action_filter:
            stmt = stmt.where(AuditLogRecord.action == action_filter)

        stmt = stmt.order_by(AuditLogRecord.created_at.desc()).limit(limit)

        result = await self._session.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "user_id": r.user_id,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "detail": r.detail,
                "created_at": r.created_at,
            }
            for r in records
        ]
    
# ═══════════════════════════════════════════════════════════
# Conversation Repository
# ═══════════════════════════════════════════════════════════


from app.db.models import ConversationRecord, MessageRecord


class ConversationRepository:
    """CRUD operations for chat conversations and messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Conversions ────────────────────────────────────

    @staticmethod
    def _conversation_to_dict(record: ConversationRecord) -> dict:
        """Convert conversation ORM record to dict (without messages)."""
        return {
            "id": record.id,
            "title": record.title,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _message_to_dict(record: MessageRecord) -> dict:
        """Convert message ORM record to dict."""
        return {
            "id": record.id,
            "role": record.role,
            "content": record.content,
            "sources": record.sources or [],
            "query_type": record.query_type,
            "cached": record.cached,
            "created_at": record.created_at,
        }

    # ── Create ─────────────────────────────────────────

    async def create_conversation(
        self,
        org_id: UUID,
        user_id: UUID,
        title: str = "New Chat",
    ) -> dict:
        """Create a new empty conversation. Returns conversation dict."""
        record = ConversationRecord(
            id=uuid4(),
            org_id=org_id,
            user_id=user_id,
            title=title,
        )
        self._session.add(record)
        await self._session.flush()
        return self._conversation_to_dict(record)

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        sources: list | None = None,
        query_type: str | None = None,
        cached: bool = False,
    ) -> dict:
        """
        Add a message to a conversation.
        Also bumps the conversation's updated_at and auto-sets title
        from the first user message.
        """
        record = MessageRecord(
            id=uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            sources=sources or [],
            query_type=query_type,
            cached=cached,
        )
        self._session.add(record)

        # Bump conversation updated_at
        await self._session.execute(
            update(ConversationRecord)
            .where(ConversationRecord.id == conversation_id)
            .values(updated_at=datetime.now(timezone.utc))
        )

        # Auto-title from first user message
        if role == "user":
            await self._maybe_set_title(conversation_id, content)

        await self._session.flush()
        return self._message_to_dict(record)

    async def _maybe_set_title(self, conversation_id: UUID, content: str) -> None:
        """Set conversation title from first user message if still default."""
        stmt = select(ConversationRecord).where(
            ConversationRecord.id == conversation_id
        )
        result = await self._session.execute(stmt)
        conv = result.scalar_one_or_none()

        if conv and conv.title == "New Chat":
            # Count existing messages to check if this is the first
            count_stmt = select(func.count(MessageRecord.id)).where(
                MessageRecord.conversation_id == conversation_id
            )
            count_result = await self._session.execute(count_stmt)
            msg_count = count_result.scalar_one()

            # If this is the first message (just added), set title
            if msg_count <= 1:
                title = content[:50] + ("..." if len(content) > 50 else "")
                await self._session.execute(
                    update(ConversationRecord)
                    .where(ConversationRecord.id == conversation_id)
                    .values(title=title)
                )

    # ── Read ───────────────────────────────────────────

    async def list_conversations(
        self,
        user_id: UUID,
        org_id: UUID,
        limit: int = 50,
    ) -> list[dict]:
        """
        List a user's conversations (most recent first).
        Does NOT include messages — just metadata for the sidebar.
        """
        stmt = (
            select(ConversationRecord)
            .where(
                ConversationRecord.user_id == user_id,
                ConversationRecord.org_id == org_id,
            )
            .order_by(ConversationRecord.updated_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        records = result.scalars().all()
        return [self._conversation_to_dict(r) for r in records]

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        org_id: UUID,
    ) -> dict | None:
        """
        Get a conversation with all its messages.
        Org + user scoped for isolation.
        """
        stmt = (
            select(ConversationRecord)
            .options(selectinload(ConversationRecord.messages))
            .where(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_id == user_id,
                ConversationRecord.org_id == org_id,
            )
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            return None

        conv = self._conversation_to_dict(record)
        conv["messages"] = [
            self._message_to_dict(m) for m in record.messages
        ]
        return conv

    async def get_recent_messages(
        self,
        conversation_id: UUID,
        limit: int = 20,
    ) -> list[dict]:
        """
        Get the most recent messages from a conversation.
        Used to build history context for the chat pipeline.
        Returns in chronological order (oldest first).
        """
        stmt = (
            select(MessageRecord)
            .where(MessageRecord.conversation_id == conversation_id)
            .order_by(MessageRecord.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        records = list(result.scalars().all())
        # Reverse to chronological order
        records.reverse()
        return [self._message_to_dict(r) for r in records]

    # ── Update ─────────────────────────────────────────

    async def rename_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        org_id: UUID,
        new_title: str,
    ) -> bool:
        """Rename a conversation. Returns True if updated."""
        stmt = (
            update(ConversationRecord)
            .where(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_id == user_id,
                ConversationRecord.org_id == org_id,
            )
            .values(
                title=new_title[:200],
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── Delete ─────────────────────────────────────────

    async def delete_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        org_id: UUID,
    ) -> bool:
        """
        Delete a conversation (cascades to messages).
        Org + user scoped. Returns True if deleted.
        """
        # Verify ownership first
        stmt = select(ConversationRecord).where(
            ConversationRecord.id == conversation_id,
            ConversationRecord.user_id == user_id,
            ConversationRecord.org_id == org_id,
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            return False

        await self._session.delete(record)  # Cascades to messages
        return True

    async def verify_ownership(
        self,
        conversation_id: UUID,
        user_id: UUID,
        org_id: UUID,
    ) -> bool:
        """Check if a conversation belongs to this user/org."""
        stmt = select(ConversationRecord.id).where(
            ConversationRecord.id == conversation_id,
            ConversationRecord.user_id == user_id,
            ConversationRecord.org_id == org_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None