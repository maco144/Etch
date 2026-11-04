"""
Database session management for Etch.

Uses async SQLAlchemy with PostgreSQL (DATABASE_URL) or SQLite fallback.
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

logger = logging.getLogger(__name__)

_DATABASE_URL = os.environ.get(
    "ETCH_DATABASE_URL",
    os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./etch.db"),
)

# Convert postgres:// to postgresql+asyncpg://
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif _DATABASE_URL.startswith("postgresql://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

_engine = create_async_engine(_DATABASE_URL, echo=False)
_session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session():
    """Yield an async database session."""
    async with _session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Columns added after tables already existed in deployed databases. create_all never alters an existing
# table, so each one is added here if missing. Nullable only: old rows stay valid without a rewrite.
_ADDED_COLUMNS = (
    ("etch_proofs", "payload_json", "TEXT"),
    ("etch_records", "payload_json", "TEXT"),
)


def _add_missing_columns(sync_conn) -> None:
    from sqlalchemy import inspect, text
    inspector = inspect(sync_conn)
    for table, column, ddl_type in _ADDED_COLUMNS:
        if not inspector.has_table(table):
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info(f"[Etch] Added column {table}.{column}")


async def create_tables():
    """Create missing tables, then add columns introduced since (see _ADDED_COLUMNS)."""
    from .models import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
