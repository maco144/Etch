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


async def create_tables():
    """Create all tables (for dev/testing). Use Alembic in production."""
    from .models import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
