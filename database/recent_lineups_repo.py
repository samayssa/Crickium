from __future__ import annotations

import json

from database.query import execute, fetchrow


async def get_recent_xi(user_id: int, engine_key: str, team_code: str):
    row = await fetchrow(
        """
        SELECT player_ids
        FROM recent_playing_xis
        WHERE user_id=$1 AND engine_key=$2 AND team_code=$3
        LIMIT 1;
        """,
        int(user_id), str(engine_key).upper(), str(team_code).upper(),
    )
    if not row:
        return None
    value = dict(row).get("player_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return [int(x) for x in (value or [])]


async def save_recent_xi(user_id: int, engine_key: str, team_code: str, player_ids):
    await execute(
        """
        INSERT INTO recent_playing_xis(user_id, engine_key, team_code, player_ids, updated_at)
        VALUES ($1,$2,$3,$4::jsonb,NOW())
        ON CONFLICT (user_id, engine_key, team_code)
        DO UPDATE SET player_ids=EXCLUDED.player_ids, updated_at=NOW();
        """,
        int(user_id), str(engine_key).upper(), str(team_code).upper(), json.dumps([int(x) for x in player_ids]),
    )
