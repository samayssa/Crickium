from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from database.connection import connect as connect_bot_database
from database.connection import disconnect as disconnect_bot_database
from database.connection import get_pool

async def connect():
    """Use the bot's provider-aware PostgreSQL pool for every Mini App query."""
    return await connect_bot_database()


async def disconnect() -> None:
    await disconnect_bot_database()


def pool():
    return get_pool()


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
