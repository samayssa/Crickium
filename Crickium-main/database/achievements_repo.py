"""Storage for unlocked achievements (one row per user + key)."""

from __future__ import annotations

from database.query import execute, fetch

_ready = False

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS user_achievements(
        user_id BIGINT NOT NULL,
        ach_key TEXT NOT NULL,
        unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(user_id, ach_key)
    );
    """,
)


async def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    for stmt in SCHEMA:
        await execute(stmt)
    _ready = True


async def unlocked(user_id: int) -> dict[str, object]:
    await ensure_schema()
    rows = await fetch("SELECT ach_key, unlocked_at FROM user_achievements WHERE user_id=$1;", int(user_id))
    return {r["ach_key"]: r["unlocked_at"] for r in rows}


async def unlock_many(user_id: int, keys: list[str]) -> list[str]:
    """Insert keys, return the ones that were actually new."""
    if not keys:
        return []
    await ensure_schema()
    rows = await fetch(
        """
        INSERT INTO user_achievements(user_id, ach_key)
        SELECT $1, k FROM UNNEST($2::text[]) AS k
        ON CONFLICT DO NOTHING
        RETURNING ach_key;
        """,
        int(user_id), list(keys),
    )
    return [r["ach_key"] for r in rows]
