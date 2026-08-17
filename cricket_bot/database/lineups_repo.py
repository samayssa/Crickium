from __future__ import annotations

import json

from database.query import execute, fetchrow


async def get_lineup_ids(user_id: int) -> list[int] | None:
    row = await fetchrow("SELECT player_ids FROM team_lineups WHERE user_id = $1;", user_id)
    if not row:
        return None
    ids = row["player_ids"]
    if isinstance(ids, str):
        return json.loads(ids)
    if isinstance(ids, list):
        return ids
    return json.loads(json.dumps(ids, default=str))


async def save_lineup_ids(user_id: int, player_ids: list[int]) -> None:
    ids_json = json.dumps(player_ids)
    await execute(
        """
        INSERT INTO team_lineups (user_id, player_ids, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET player_ids = EXCLUDED.player_ids, updated_at = NOW();
        """,
        user_id, ids_json,
    )
