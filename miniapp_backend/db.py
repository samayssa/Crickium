from __future__ import annotations

import time
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


def _note_statement(query: str, result: Any, started: float, *, write: bool = False) -> None:
    try:
        from database import db_stats
        db_stats.record(query, result, started, write=write)
    except Exception:
        pass


async def fetchrow(query: str, *args: Any):
    started = time.monotonic()
    async with acquire() as conn:
        result = await conn.fetchrow(query, *args)
    _note_statement(query, result, started)
    return result


async def fetch(query: str, *args: Any):
    started = time.monotonic()
    async with acquire() as conn:
        result = await conn.fetch(query, *args)
    _note_statement(query, result, started)
    return result


async def execute(query: str, *args: Any):
    started = time.monotonic()
    async with acquire() as conn:
        result = await conn.execute(query, *args)
    _note_statement(query, result, started, write=True)
    return result


async def fetchval(query: str, *args: Any):
    started = time.monotonic()
    async with acquire() as conn:
        result = await conn.fetchval(query, *args)
    _note_statement(query, result, started)
    return result
