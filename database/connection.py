import time

import asyncpg

from config import DATABASE_URL

_pool = None
_DB_BLOCKED_UNTIL = 0.0
_DB_BLOCK_REASON = None
_DB_COOLDOWN_SECONDS = 30.0


def _quota_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return (
        "insufficientresourceserror" in name
        or "exceeded the quota" in text
        or "quota" in text and ("exceeded" in text or "limit" in text)
    )


def is_database_quota_blocked() -> bool:
    return time.monotonic() < _DB_BLOCKED_UNTIL


def database_block_reason() -> str | None:
    return _DB_BLOCK_REASON


def mark_database_quota_exhausted(exc: BaseException) -> None:
    global _DB_BLOCKED_UNTIL, _DB_BLOCK_REASON
    _DB_BLOCKED_UNTIL = max(_DB_BLOCKED_UNTIL, time.monotonic() + _DB_COOLDOWN_SECONDS)
    _DB_BLOCK_REASON = str(exc) or "Database provider quota is temporarily exhausted."
    print(
        f"[db/connection] Database quota circuit-breaker active for "
        f"{_DB_COOLDOWN_SECONDS:.0f}s: {_DB_BLOCK_REASON}"
    )


def clear_database_quota_block() -> None:
    global _DB_BLOCKED_UNTIL, _DB_BLOCK_REASON
    _DB_BLOCKED_UNTIL = 0.0
    _DB_BLOCK_REASON = None


def is_database_quota_error(exc: BaseException) -> bool:
    return _quota_error(exc)


async def connect():
    global _pool

    print("[db/connection] connect() called")

    if is_database_quota_blocked():
        raise RuntimeError("Database provider quota is temporarily exhausted; retry shortly.")

    if _pool is None:
        print("[db/connection] No existing pool, creating new asyncpg pool...")
        try:
            _pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=0,
                max_size=5
            )
            clear_database_quota_block()
            print("[db/connection] Pool created successfully.")
        except Exception as e:
            if _quota_error(e):
                mark_database_quota_exhausted(e)
            print(f"[db/connection] !! Failed to create pool: {e!r}")
            raise
    else:
        print("[db/connection] Pool already exists, reusing it.")

    return _pool


async def disconnect():
    global _pool

    print("[db/connection] disconnect() called")

    if _pool:
        await _pool.close()
        _pool = None
        print("[db/connection] Pool closed.")
    else:
        print("[db/connection] No pool to close.")


def get_pool():
    if is_database_quota_blocked():
        raise RuntimeError("Database provider quota is temporarily exhausted; retry shortly.")

    if _pool is None:
        print("[db/connection] !! get_pool() called but pool is None !!")
        raise RuntimeError("Database is not connected.")

    return _pool
