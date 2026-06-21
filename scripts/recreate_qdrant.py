"""
scripts/recreate_qdrant.py

Destructive maintenance: recreate the Qdrant collection at the ACTIVE embedding
provider's dimension and (optionally) truncate the Postgres `documents` table so
the system is ready for a clean re-ingest.

WHY: switching embedding_provider voyage(1024) -> openai(1536) changes the vector
dimension. Qdrant collections are fixed-dimension, so the old collection cannot
hold the new vectors — it must be dropped and recreated. Old documents rows point
to vectors that no longer exist, so they should be truncated and re-ingested.

This script is intentionally guarded:
  - Dry-run by default: prints what WOULD happen, changes nothing.
  - --yes              actually drop + recreate the Qdrant collection.
  - --truncate-db      ALSO truncate the documents table (CASCADE).
Both destructive flags must be passed explicitly.

Usage:
    # See the plan (no changes):
    python scripts/recreate_qdrant.py

    # Recreate Qdrant collection only:
    python scripts/recreate_qdrant.py --yes

    # Recreate Qdrant collection AND truncate documents table:
    python scripts/recreate_qdrant.py --yes --truncate-db

Reads all connection config from environment (.env / QUILL_* vars), so point it
at prod by exporting the Neon + Qdrant Cloud credentials before running.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import models
from sqlalchemy import text

from config import get_settings
from config.constants import QDRANT_DENSE_VECTOR_NAME

logger = structlog.get_logger(__name__)


def _make_client(settings) -> AsyncQdrantClient:
    if settings.qdrant_use_cloud:
        return AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value(),
            prefer_grpc=False,
            timeout=30,
        )
    return AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=settings.qdrant_prefer_grpc,
    )


async def recreate_collection(client: AsyncQdrantClient, name: str, dims: int) -> None:
    """Drop (if exists) and create the collection at the given dimension."""
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]

    if name in existing:
        print(f"  dropping existing collection '{name}' ...")
        await client.delete_collection(collection_name=name)

    print(f"  creating collection '{name}' at {dims} dims (cosine) ...")
    await client.create_collection(
        collection_name=name,
        vectors_config={
            QDRANT_DENSE_VECTOR_NAME: models.VectorParams(
                size=dims,
                distance=models.Distance.COSINE,
                on_disk=False,
            ),
        },
        hnsw_config=models.HnswConfigDiff(
            m=16, ef_construct=100, full_scan_threshold=10000
        ),
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=20000),
    )
    for field in ("org_id", "source_doc_id", "source_filename"):
        await client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    print(f"  collection '{name}' recreated with payload indexes.")


async def truncate_documents() -> None:
    """Truncate the documents table (CASCADE removes ingestion/job links)."""
    from app.db.engine import _build_engine  # local import to avoid load if unused

    engine = _build_engine()
    try:
        async with engine.begin() as conn:
            # CASCADE clears job_documents / ingestion-linked rows referencing documents.
            await conn.execute(text("TRUNCATE TABLE documents CASCADE"))
    finally:
        await engine.dispose()
    print("  documents table truncated (CASCADE).")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Recreate Qdrant collection / truncate documents")
    parser.add_argument("--yes", action="store_true", help="actually recreate the Qdrant collection")
    parser.add_argument("--truncate-db", action="store_true", help="also TRUNCATE the documents table")
    args = parser.parse_args()

    settings = get_settings()
    name = settings.qdrant_collection_name
    dims = settings.active_embedding_dimensions

    print("== Qdrant recreate plan =========================")
    print(f"  embedding provider : {settings.embedding_provider}")
    print(f"  target collection  : {name}")
    print(f"  target dimensions  : {dims}")
    print(f"  qdrant target      : {'cloud' if settings.qdrant_use_cloud else f'{settings.qdrant_host}:{settings.qdrant_port}'}")
    print(f"  truncate documents : {'YES' if args.truncate_db else 'no'}")
    print("=================================================")

    if not args.yes:
        print("DRY RUN - nothing changed. Re-run with --yes to recreate the collection.")
        if args.truncate_db:
            print("           (--truncate-db will only run together with --yes)")
        return 0

    client = _make_client(settings)
    try:
        await recreate_collection(client, name, dims)
        if args.truncate_db:
            await truncate_documents()
    finally:
        await client.close()

    print("Done. Re-ingest documents to repopulate the collection.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
