"""SQLAlchemy 2.0 async engine/session setup.

This is the persistence layer chosen for the Python/FastAPI backend
(Alembic-managed), per Sprint 1's own directory layout and Acceptance
Checklist ("alembic upgrade head"). See the Sprint 1 engineering brief,
Section 7.2, for why SATDD's Prisma illustration is treated as non-binding.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, future=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def tenant_scoped_session(
    session_factory: async_sessionmaker[AsyncSession], store_id: str | None = None
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with the Postgres RLS tenant GUC set for this request.

    Per the Technical Blueprint's multi-tenancy axiom: "Before executing any
    database transaction, the API session sets the session local variable:
    SET LOCAL app.current_store_id = '<tenant-uuid>'." Application-layer
    store-scoping in repository queries is a defense-in-depth measure, not a
    substitute for this DB-level RLS policy (brief Section 8.4).
    """
    async with session_factory() as session:
        async with session.begin():
            if store_id is not None:
                await session.execute(
                    text("SET LOCAL app.current_store_id = :store_id"),
                    {"store_id": store_id},
                )
            yield session
