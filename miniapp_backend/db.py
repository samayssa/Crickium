from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from .config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    conn = await pool().acquire()
    try:
        yield conn
    finally:
        await pool().release(conn)


async def fetchrow(query: str, *args: Any):
    async with acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch(query: str, *args: Any):
    async with acquire() as conn:
        return await conn.fetch(query, *args)


async def execute(query: str, *args: Any):
    async with acquire() as conn:
        return await conn.execute(query, *args)


async def fetchval(query: str, *args: Any):
    async with acquire() as conn:
        return await conn.fetchval(query, *args)
