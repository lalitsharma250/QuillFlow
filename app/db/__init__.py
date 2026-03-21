"""
app/db — Database layer for QuillFlow.

Uses async SQLAlchemy 2.0 with asyncpg driver.

Components:
  - engine.py:     Connection pool + session factory
  - models.py:     ORM table definitions
  - repository.py: CRUD operations (no raw SQL in services/API)
"""
