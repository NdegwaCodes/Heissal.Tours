"""Async database engine, session factory, and FastAPI dependency.

Importing this module also imports the full model registry (``app.db.base``).
That is deliberate: a cross-module foreign key (``accommodation_rates`` ->
``supplier_documents``) only resolves if BOTH tables are present in
``Base.metadata``, and SQLAlchemy resolves it lazily — at the first flush, not at
import — so a missing model surfaced as a runtime NoReferencedTableError on an
endpoint rather than as an import error. Anything that opens a session now has
the complete metadata by construction. No model imports this module, so there is
no cycle.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.base  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings

engine = create_async_engine(
    settings.sqlalchemy_database_uri,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args=settings.async_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session, rolling back on error and always closing."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
