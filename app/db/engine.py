"""
app/db/engine.py

Async SQLAlchemy engine and session management.

Lifecycle:
  - init_db() called during FastAPI lifespan startup
  - Stores engine + session factory on app.state
  - close_db() called during shutdown
  - get_db_session() dependency yields per-request sessions
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from fastapi import FastAPI

from config import get_settings


def _build_engine() -> AsyncEngine:
    """
    Create the async SQLAlchemy engine.

    Key settings:
      - pool_size: Number of persistent connections
      - max_overflow: Extra connections allowed under load
      - pool_pre_ping: Detect stale connections before use
      - echo: Log SQL statements (only in debug mode)
    """
    settings = get_settings()

    return create_async_engine(
        settings.postgres_dsn,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,       # Recycle connections after 1 hour
        echo=settings.debug,     # SQL logging in debug mode only
    )


def _build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Create a session factory bound to the engine.

    expire_on_commit=False:
      After commit, we can still access attributes without
      triggering a lazy load (which would fail outside a session).
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_db(app: FastAPI) -> None:
    """
    Initialize the database connection pool.
    Called during FastAPI lifespan startup.

    Stores on app.state:
      - db_engine: The SQLAlchemy async engine
      - db_session_factory: Session factory for creating per-request sessions
    """
    engine = _build_engine()
    session_factory = _build_session_factory(engine)

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory


async def close_db(app: FastAPI) -> None:
    """
    Dispose of the connection pool.
    Called during FastAPI lifespan shutdown.
    """
    engine: AsyncEngine | None = getattr(app.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()


async def create_tables(engine: AsyncEngine) -> None:
    """
    Create all tables defined in ORM models.

    USE ONLY FOR:
      - Tests (create fresh tables per test run)
      - Initial local dev setup

    In production, use Alembic migrations instead.
    """
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables(engine: AsyncEngine) -> None:
    """Drop all tables. USE ONLY IN TESTS."""
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)