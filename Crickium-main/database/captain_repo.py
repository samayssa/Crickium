from __future__ import annotations

from database.query import execute, fetchval


async def get_captain_id(user_id: int) -> int | None:
    value = await fetchval("SELECT captain_player_id FROM users WHERE user_id = $1;", user_id)
    return int(value) if value is not None else None


async def set_captain_id(user_id: int, player_id: int) -> None:
    await execute(
        "UPDATE users SET captain_player_id = $1 WHERE user_id = $2;",
        int(player_id), int(user_id),
    )
