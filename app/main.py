"""
app/main.py

FastAPI application factory with lifespan management.
All external connections (Qdrant, Redis, Postgres) are initialized at startup
and cleaned up at shutdown. No connection is created at import time.
"""

import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.engine import init_db, close_db
from app.services.retrieval.vector_store import init_qdrant, close_qdrant
from app.services.cache.connection import init_redis, close_redis
from app.services.retrieval.embedder import init_embedder
from app.api.router import api_router
from config.logging import setup_logging
from arq.connections import create_pool, RedisSettings
from app.services.retrieval.reranker import RerankerService, NoOpReranker
from app.services.llm.client import LLMClient
from app.services.guardrails.output_validator import OutputValidator
from app.graph.builder import build_graph
from app.services.cache.manager import CacheManager
from app.services.retrieval.hybrid import HybridRetriever
from starlette.middleware.trustedhost import TrustedHostMiddleware
from config import get_settings
from starlette.middleware.cors import CORSMiddleware

logger = structlog.get_logger(__name__)

async def _run_migrations():
    """Run Alembic migrations on startup (production deployments)."""
    import asyncio
    
    settings = get_settings()
    
    # Skip in local dev (use Docker migrate container)
    if settings.debug:
        return
    
    try:
        # Run alembic upgrade head in a subprocess
        proc = await asyncio.create_subprocess_exec(
            "alembic", "upgrade", "head",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            logger.info("migrations_applied", output=stdout.decode()[:500])
        else:
            logger.error(
                "migration_failed",
                stderr=stderr.decode()[:500],
                returncode=proc.returncode,
            )
    except Exception as e:
        logger.error("migration_error", error=str(e))

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manages the lifecycle of external connections.

    Startup:
      1. Initialize Postgres connection pool
      2. Connect to Qdrant
      3. Connect to Redis
      4. Load embedding model into memory
      5. Load reranker model (optional)
      6. Initialize LLM client
      7. Initialize output validator
      8. Compile LangGraph DAG
      9. Connect ARQ job pool

    Shutdown:
      Gracefully close all connections in reverse order.
    """
    start_time = time.monotonic()
    settings = get_settings()

    logger.info(
        "starting_quillflow",
        app_name=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )

    # ── Startup ────────────────────────────────────────
    
    await _run_migrations()
    # 1. Postgres
    await init_db(app)
    logger.info("postgres_connected", host=settings.postgres_host)

    # 2. Qdrant
    await init_qdrant(app)
    logger.info("qdrant_connected", host=settings.qdrant_host)

    # 3. Redis
    await init_redis(app)
    logger.info("redis_connected", url=settings.redis_url)

    # 4. Embedding model
    await init_embedder(app)
    logger.info("embedding_model_loaded", model=settings.embedding_model_name)

    # 5. Reranker service (Voyage AI — gracefully degrades if unavailable)
    try:
        reranker = RerankerService()
        await reranker.load()
        app.state.reranker = reranker
        logger.info("reranker_ready", model=reranker.model_name)
    except Exception as e:
        logger.warning(
            "reranker_init_failed_using_noop",
            error=str(e),
        )
        app.state.reranker = NoOpReranker()

    # 6. LLM Client
    llm_client = LLMClient()
    app.state.llm_client = llm_client
    logger.info("llm_client_initialized")

    # 7. Output Validator
    app.state.output_validator = OutputValidator(llm_client=llm_client)
    logger.info("output_validator_initialized")

    # 8. Compile the LangGraph DAG
    if app.state.embedder is not None and getattr(app.state, "vector_store", None) is not None:
        hybrid_retriever = HybridRetriever(
            embedder=app.state.embedder,
            vector_store=app.state.vector_store,
            reranker=app.state.reranker,
        )
        app.state.hybrid_retriever = hybrid_retriever

        cache_manager = CacheManager(redis=app.state.redis_client)
        app.state.cache_manager = cache_manager

        compiled_graph = build_graph(
            llm_client=llm_client,
            embedder=app.state.embedder,
            hybrid_retriever=hybrid_retriever,
            cache_manager=cache_manager,
            output_validator=app.state.output_validator,
        )
        app.state.compiled_graph = compiled_graph
        logger.info("langgraph_dag_compiled")
    else:
        logger.error(
            "graph_compilation_skipped_missing_dependencies",
            has_embedder=app.state.embedder is not None,
            has_vector_store=getattr(app.state, "vector_store", None) is not None,
        )
        app.state.compiled_graph = None
        app.state.hybrid_retriever = None
        app.state.cache_manager = CacheManager(redis=app.state.redis_client)

    # 9. ARQ job pool (for enqueuing background tasks)
    try:
        arq_settings = RedisSettings(
            host=settings.worker_redis_settings["host"],
            port=settings.worker_redis_settings["port"],
            database=settings.worker_redis_settings["database"],
        )
        app.state.arq_pool = await create_pool(arq_settings)
        logger.info("arq_pool_connected")
    except Exception as e:
        logger.warning(
            "arq_pool_connection_failed",
            error=str(e),
        )
        app.state.arq_pool = None

    startup_duration = time.monotonic() - start_time
    logger.info(
        "quillflow_ready",
        port=settings.api_port,
        startup_seconds=round(startup_duration, 2),
    )
    await _cleanup_stuck_documents(app)
    yield  # ── App is running ──

    # ── Shutdown ───────────────────────────────────────
    logger.info("shutting_down_quillflow")

    if app.state.arq_pool is not None:
        await app.state.arq_pool.close()
    
    # Close embedder and reranker HTTP clients
    if hasattr(app.state, "embedder"):
        await app.state.embedder.close()
    if hasattr(app.state, "reranker") and hasattr(app.state.reranker, "close"):
        await app.state.reranker.close()
    
    await close_redis(app)
    await close_qdrant(app)
    await close_db(app)

    logger.info("quillflow_stopped")


def create_app() -> FastAPI:
    """
    Application factory. Returns a configured FastAPI instance.

    Why a factory?
    - Tests can create isolated app instances
    - Different configs for dev/staging/prod
    - Avoids module-level side effects
    """
    settings = get_settings()

    # Initialize logging FIRST — before anything else logs
    setup_logging()
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Production-grade Agentic RAG content generation system",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # ── Middleware ──────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────
    app.include_router(api_router)

    return app

async def _cleanup_stuck_documents(app: FastAPI):
    """Reset documents stuck in 'processing' status from previous crashes."""
    from sqlalchemy import update
    from app.db.models import DocumentRecord
    
    try:
        async with app.state.db_session_factory() as session:
            result = await session.execute(
                update(DocumentRecord)
                .where(DocumentRecord.status == "processing")
                .values(status="pending")
            )
            if result.rowcount > 0:
                await session.commit()
                logger.info("stuck_documents_reset", count=result.rowcount)
    except Exception as e:
        logger.warning("stuck_document_cleanup_failed", error=str(e))