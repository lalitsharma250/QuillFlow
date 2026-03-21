"""
tests/integration/test_db.py

Integration tests for the database layer.
Requires Postgres (from docker-compose).
"""

import pytest
from uuid import uuid4

from app.db.repository import (
    DocumentRepository,
    IngestionJobRepository,
    LineageRepository,
    AuditRepository,
)


class TestDocumentRepository:
    async def test_create_and_get(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        doc = await repo.create_document(
            org_id=org_id,
            filename="test.pdf",
            content_type="pdf",
            raw_text="Hello world",
        )
        await session.flush()

        fetched = await repo.get_document(doc.id, org_id)
        assert fetched is not None
        assert fetched.filename == "test.pdf"
        assert fetched.status.value == "pending"

    async def test_org_isolation(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        doc = await repo.create_document(
            org_id=org_id,
            filename="isolated.txt",
            content_type="text",
            raw_text="content",
        )
        await session.flush()

        # Same org — found
        assert await repo.get_document(doc.id, org_id) is not None

        # Different org — not found
        other_org = uuid4()
        assert await repo.get_document(doc.id, other_org) is None

    async def test_batch_create(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        docs = await repo.create_documents_batch(
            org_id=org_id,
            documents=[
                {"filename": "a.txt", "content_type": "text", "raw_text": "A"},
                {"filename": "b.txt", "content_type": "text", "raw_text": "B"},
                {"filename": "c.txt", "content_type": "text", "raw_text": "C"},
            ],
        )
        await session.flush()

        assert len(docs) == 3
        assert all(d.status.value == "pending" for d in docs)

    async def test_update_status(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        doc = await repo.create_document(
            org_id=org_id,
            filename="status.txt",
            content_type="text",
            raw_text="content",
        )
        await session.flush()

        await repo.update_document_status(doc.id, "indexed", chunk_count=15)
        await session.flush()

        fetched = await repo.get_document(doc.id, org_id)
        assert fetched.status.value == "indexed"
        assert fetched.chunk_count == 15

    async def test_list_with_pagination(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        for i in range(5):
            await repo.create_document(
                org_id=org_id,
                filename=f"doc_{i}.txt",
                content_type="text",
                raw_text=f"Content {i}",
            )
        await session.flush()

        docs, total = await repo.list_documents(org_id=org_id, page=1, page_size=2)
        assert total == 5
        assert len(docs) == 2

        docs2, _ = await repo.list_documents(org_id=org_id, page=3, page_size=2)
        assert len(docs2) == 1

    async def test_list_with_status_filter(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = DocumentRepository(session)

        doc1 = await repo.create_document(org_id=org_id, filename="good.txt", content_type="text", raw_text="OK")
        doc2 = await repo.create_document(org_id=org_id, filename="bad.txt", content_type="text", raw_text="Fail")
        await session.flush()

        await repo.update_document_status(doc1.id, "indexed")
        await repo.update_document_status(doc2.id, "failed", error_message="Parse error")
        await session.flush()

        docs, total = await repo.list_documents(org_id=org_id, status_filter="indexed")
        assert total == 1
        assert docs[0].filename == "good.txt"


class TestIngestionJobRepository:
    async def test_create_and_get_job(self, db_session_with_org):
        session, org_id = db_session_with_org
        doc_repo = DocumentRepository(session)
        job_repo = IngestionJobRepository(session)

        docs = await doc_repo.create_documents_batch(
            org_id=org_id,
            documents=[
                {"filename": "j1.txt", "content_type": "text", "raw_text": "A"},
                {"filename": "j2.txt", "content_type": "text", "raw_text": "B"},
            ],
        )
        await session.flush()

        job = await job_repo.create_job(
            org_id=org_id,
            document_ids=[d.id for d in docs],
        )
        await session.flush()

        assert job.total_documents == 2
        assert job.status.value == "accepted"

        fetched = await job_repo.get_job(job.id, org_id)
        assert fetched is not None
        assert fetched.id == job.id

    async def test_job_progress(self, db_session_with_org):
        session, org_id = db_session_with_org
        doc_repo = DocumentRepository(session)
        job_repo = IngestionJobRepository(session)

        docs = await doc_repo.create_documents_batch(
            org_id=org_id,
            documents=[
                {"filename": f"p{i}.txt", "content_type": "text", "raw_text": f"T{i}"}
                for i in range(5)
            ],
        )
        await session.flush()

        job = await job_repo.create_job(org_id=org_id, document_ids=[d.id for d in docs])
        await session.flush()

        await job_repo.update_job_status(job.id, "processing")
        await job_repo.update_job_progress(job.id, processed_documents=3, failed_documents=1)
        await session.flush()

        fetched = await job_repo.get_job(job.id, org_id)
        assert fetched.processed_documents == 3
        assert fetched.failed_documents == 1

    async def test_job_with_document_statuses(self, db_session_with_org):
        session, org_id = db_session_with_org
        doc_repo = DocumentRepository(session)
        job_repo = IngestionJobRepository(session)

        docs = await doc_repo.create_documents_batch(
            org_id=org_id,
            documents=[
                {"filename": "ok.txt", "content_type": "text", "raw_text": "OK"},
                {"filename": "fail.txt", "content_type": "text", "raw_text": "Fail"},
            ],
        )
        await session.flush()

        job = await job_repo.create_job(org_id=org_id, document_ids=[d.id for d in docs])
        await session.flush()

        await doc_repo.update_document_status(docs[0].id, "indexed", chunk_count=10)
        await doc_repo.update_document_status(docs[1].id, "failed", error_message="Bad format")
        await session.flush()

        fetched_job, doc_statuses = await job_repo.get_job_with_document_statuses(job.id, org_id)
        assert fetched_job is not None
        assert len(doc_statuses) == 2

        status_map = {ds["filename"]: ds for ds in doc_statuses}
        assert status_map["ok.txt"]["status"] == "indexed"
        assert status_map["fail.txt"]["status"] == "failed"


class TestLineageRepository:
    async def test_record_and_fetch(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = LineageRepository(session)
        response_id = uuid4()

        await repo.record_lineage(
            response_id=response_id,
            query_text="What is RAG?",
            chunks=[
                {
                    "chunk_id": uuid4(),
                    "chunk_text_preview": "RAG combines retrieval...",
                    "similarity_score": 0.95,
                    "retrieval_method": "dense",
                    "document_version": 1,
                },
                {
                    "chunk_id": uuid4(),
                    "chunk_text_preview": "Vector databases store...",
                    "similarity_score": 0.88,
                    "retrieval_method": "hybrid",
                    "document_version": 1,
                },
            ],
        )
        await session.flush()

        lineage = await repo.get_response_lineage(response_id)
        assert len(lineage) == 2
        assert lineage[0]["similarity_score"] == 0.95  # Ordered by score desc


class TestAuditRepository:
    async def test_log_and_fetch(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = AuditRepository(session)
        user_id = uuid4()

        await repo.log(
            action="query",
            user_id=user_id,
            org_id=org_id,
            resource_type="chat",
            resource_id=uuid4(),
            detail={"query_preview": "What is RAG?"},
        )
        await session.flush()

        activity = await repo.get_user_activity(user_id, limit=10)
        assert len(activity) == 1
        assert activity[0]["action"] == "query"

    async def test_org_activity(self, db_session_with_org):
        session, org_id = db_session_with_org
        repo = AuditRepository(session)

        for action in ["query", "ingest_single", "query", "ingest_bulk"]:
            await repo.log(
                action=action,
                user_id=uuid4(),
                org_id=org_id,
            )
        await session.flush()

        all_activity = await repo.get_org_activity(org_id, limit=10)
        assert len(all_activity) == 4

        queries_only = await repo.get_org_activity(org_id, action_filter="query", limit=10)
        assert len(queries_only) == 2