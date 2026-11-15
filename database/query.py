
import asyncio
import os
import random
import re
import time

from database.connection import (
    get_pool,
    is_database_quota_error,
    is_retryable_transaction_error,
    mark_database_quota_exhausted,
)

                                                                                                                             
_LOG = os.getenv("DB_QUERY_LOG", "0") == "1"
MAX_LOG_VALUE = 180
MAX_LOG_QUERY = 120


def _short(query):
    return " ".join(query.split())[:MAX_LOG_QUERY]


def _short_value(value):
    if isinstance(value, str):
        if len(value) <= MAX_LOG_VALUE:
            return value
        return value[:MAX_LOG_VALUE] + f"...({len(value)} chars)"
    if isinstance(value, (list, tuple)):
        return [_short_value(v) for v in value]
    if isinstance(value, dict):
        keys = list(value.keys())
        preview = {k: _short_value(value[k]) for k in keys[:6]}
        if len(keys) > 6:
            preview["..."] = f"+{len(keys)-6} more keys"
        return preview
    return value


def _short_args(args):
    return tuple(_short_value(arg) for arg in args)


# --- cache/diagnostics hooks -------------------------------------------------
# Counts rows/bytes per statement (database/db_stats.py) and drops cached
# catalogue data right after any statement that writes a catalogue table.
# Both are best effort and can never change a query result.
_CATALOG_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:ONLY\s+)?(?:public\.)?"
    r"(?:players|special_edition_players|showcase_cards|showcase_sets|player_card_images|"
    r"special_player_card_images|showcase_player_card_images|template_card_image|"
    r"tier_card_images|stadium_images)\b",
    re.IGNORECASE,
)


def _note_statement(query, result, started, *, write=False):
    try:
        from database import db_stats
        db_stats.record(query, result, started, write=write)
        text = str(query)
        if not text.lstrip()[:6].upper() == "SELECT" and _CATALOG_WRITE_RE.search(text):
            from database import cache
            cache.invalidate_tag("catalog")
    except Exception:
        pass


async def execute(query, *args):
    if _LOG:
        print(f"[db/query] EXECUTE: {_short(query)} | args={_short_args(args)}")
    pool = get_pool()

    started = time.monotonic()
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(query, *args)
        _note_statement(query, result, started, write=True)
        if _LOG:
            print(f"[db/query] EXECUTE OK: {result}")
        return result
    except Exception as e:
        if is_database_quota_error(e):
            mark_database_quota_exhausted(e)
        print(f"[db/query] !! EXECUTE FAILED: {e!r}")
        raise


async def executemany(query, args):
    if _LOG:
        print(f"[db/query] EXECUTEMANY: {_short(query)} | {len(args)} row(s)")
    pool = get_pool()

    started = time.monotonic()
    try:
        async with pool.acquire() as conn:
            result = await conn.executemany(query, args)
        _note_statement(query, result, started, write=True)
        if _LOG:
            print("[db/query] EXECUTEMANY OK")
        return result
    except Exception as e:
        if is_database_quota_error(e):
            mark_database_quota_exhausted(e)
        print(f"[db/query] !! EXECUTEMANY FAILED: {e!r}")
        raise


async def fetch(query, *args):
    if _LOG:
        print(f"[db/query] FETCH: {_short(query)} | args={_short_args(args)}")
    pool = get_pool()

    started = time.monotonic()
    try:
        async with pool.acquire() as conn:
            result = await conn.fetch(query, *args)
        _note_statement(query, result, started)
        if _LOG:
            print(f"[db/query] FETCH OK: {len(result)} row(s)")
        return result
    except Exception as e:
        if is_database_quota_error(e):
            mark_database_quota_exhausted(e)
        print(f"[db/query] !! FETCH FAILED: {e!r}")
        raise


async def fetchrow(query, *args):
    if _LOG:
        print(f"[db/query] FETCHROW: {_short(query)} | args={_short_args(args)}")
    pool = get_pool()

    started = time.monotonic()
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchrow(query, *args)
        _note_statement(query, result, started)
        if _LOG:
            print(f"[db/query] FETCHROW OK: {None if result is None else _short_value(dict(result))}")
        return result
    except Exception as e:
        if is_database_quota_error(e):
            mark_database_quota_exhausted(e)
        print(f"[db/query] !! FETCHROW FAILED: {e!r}")
        raise


async def fetchval(query, *args):
    if _LOG:
        print(f"[db/query] FETCHVAL: {_short(query)} | args={_short_args(args)}")
    pool = get_pool()

    started = time.monotonic()
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval(query, *args)
        _note_statement(query, result, started)
        if _LOG:
            print(f"[db/query] FETCHVAL OK: {_short_value(result)}")
        return result
    except Exception as e:
        if is_database_quota_error(e):
            mark_database_quota_exhausted(e)
        print(f"[db/query] !! FETCHVAL FAILED: {e!r}")
        raise


async def transaction(callback, *, max_retries: int = 5):
    """Run one callback in a DB transaction, retrying Cockroach serialization conflicts.

    CockroachDB can abort a serializable transaction with SQLSTATE 40001 when
    concurrent game/user actions contend. The callback is retried only for that
    specific condition; all other exceptions propagate unchanged.
    """
    pool = get_pool()
    attempts = max(1, int(max_retries))

    for attempt in range(attempts):
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    result = await callback(conn)
            return result
        except Exception as e:
            if is_database_quota_error(e):
                mark_database_quota_exhausted(e)

            if is_retryable_transaction_error(e) and attempt < attempts - 1:
                                                                              
                                                             
                delay = min(0.80, 0.05 * (2 ** attempt)) + random.uniform(0.0, 0.05)
                print(
                    f"[db/query] TRANSACTION retry {attempt + 1}/{attempts - 1} "
                    f"after Cockroach serialization conflict; sleeping {delay:.3f}s"
                )
                await asyncio.sleep(delay)
                continue

            print(f"[db/query] !! TRANSACTION FAILED (rolled back): {e!r}")
            raise

    raise RuntimeError("Database transaction retry loop exited unexpectedly.")
