"""Database setup shared by the dashboard API and gateway MCP handlers."""

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./gateway.db")


engine = create_async_engine(_database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
