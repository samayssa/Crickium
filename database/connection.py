import asyncpg

from config import DATABASE_URL

_pool = None


async def connect():
    global _pool

    print("[db/connection] connect() called")

    if _pool is None:
        print("[db/connection] No existing pool, creating new asyncpg pool...")
        try:
            _pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=2,
                max_size=20
            )
            print("[db/connection] Pool created successfully.")
        except Exception as e:
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
    if _pool is None:
        print("[db/connection] !! get_pool() called but pool is None !!")
        raise RuntimeError("Database is not connected.")

    return _pool
