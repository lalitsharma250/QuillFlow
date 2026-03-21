"""
scripts/seed_qdrant.py

Seed script for initial Qdrant setup and test data loading.

Usage:
    python -m scripts.seed_qdrant
    python -m scripts.seed_qdrant --sample-data   # Load sample documents
"""

import asyncio
import argparse
from uuid import uuid4

from config import get_settings
from config.logging import setup_logging


async def ensure_collection():
    """Create the Qdrant collection if it doesn't exist."""
    from qdrant_client import AsyncQdrantClient
    from app.services.retrieval.vector_store import VectorStoreService

    settings = get_settings()

    client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )

    service = VectorStoreService(client=client)
    await service.ensure_collection()

    info = await service.get_collection_info()
    print(f"Collection info: {info}")

    await client.close()


async def load_sample_data():
    """Load sample documents for development/testing."""
    from qdrant_client import AsyncQdrantClient
    from app.services.retrieval.embedder import EmbeddingService
    from app.services.retrieval.vector_store import VectorStoreService
    from app.services.ingestion.pipeline import IngestionPipeline
    from app.services.ingestion.chunker import TextChunker

    settings = get_settings()

    # Initialize services
    client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )
    embedder = EmbeddingService()
    await embedder.load()

    vector_store = VectorStoreService(client=client)
    await vector_store.ensure_collection()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
    )

    # Sample documents
    sample_org_id = uuid4()
    samples = [
        {
            "filename": "rag_overview.txt",
            "content_type": "text",
            "text": (
                "Retrieval-Augmented Generation (RAG) is a technique that combines "
                "information retrieval with text generation. In a RAG system, a user's "
                "query is first used to retrieve relevant documents from a knowledge base. "
                "These documents are then provided as context to a large language model, "
                "which generates a response grounded in the retrieved information. "
                "RAG helps reduce hallucinations and provides up-to-date information "
                "that may not be in the model's training data."
            ),
        },
        {
            "filename": "transformer_architecture.txt",
            "content_type": "text",
            "text": (
                "The Transformer architecture was introduced in the paper 'Attention Is "
                "All You Need' by Vaswani et al. in 2017. It relies entirely on "
                "self-attention mechanisms, dispensing with recurrence and convolutions. "
                "The key innovation is the multi-head attention mechanism, which allows "
                "the model to attend to different positions in the input sequence "
                "simultaneously. Transformers have become the foundation for modern "
                "language models including BERT, GPT, and Claude."
            ),
        },
        {
            "filename": "vector_databases.txt",
            "content_type": "text",
            "text": (
                "Vector databases are specialized databases designed to store and query "
                "high-dimensional vectors efficiently. They use approximate nearest "
                "neighbor (ANN) algorithms like HNSW to find similar vectors quickly. "
                "Popular vector databases include Qdrant, Pinecone, Weaviate, and Milvus. "
                "Qdrant is written in Rust and offers both dense and sparse vector search "
                "with payload filtering. Vector databases are essential components in "
                "RAG systems for storing document embeddings."
            ),
        },
    ]

    for sample in samples:
        doc_id = uuid4()
        result = await pipeline.process_document(
            document_id=doc_id,
            org_id=sample_org_id,
            raw_text=sample["text"],
            filename=sample["filename"],
            content_type=sample["content_type"],
        )
        print(f"Ingested '{sample['filename']}': {result.chunk_count} chunks")

    info = await vector_store.get_collection_info()
    print(f"\nCollection after seeding: {info}")
    print(f"Sample org_id: {sample_org_id}")

    await client.close()


async def main():
    parser = argparse.ArgumentParser(description="Seed Qdrant collection")
    parser.add_argument(
        "--sample-data",
        action="store_true",
        help="Load sample documents for development",
    )
    args = parser.parse_args()

    setup_logging()
    await ensure_collection()

    if args.sample_data:
        await load_sample_data()


if __name__ == "__main__":
    asyncio.run(main())
