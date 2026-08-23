"""Helpers for storing and reading a user's squad from team_squads."""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetchrow


async def get_team_squad(user_id: int) -> list[dict[str, Any]] | None:
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id = $1;", user_id)
    if not row:
        return None

    squad = row["squad"]
    if isinstance(squad, str):
        return json.loads(squad)
    if isinstance(squad, list):
        return squad
    return json.loads(json.dumps(squad, default=str))


async def save_team_squad(user_id: int, squad: list[dict[str, Any]]) -> None:
    squad_json = json.dumps(squad, default=str)
    await execute(
        """
        INSERT INTO team_squads (user_id, squad, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET squad = EXCLUDED.squad, updated_at = NOW();
        """,
        user_id, squad_json,
    )


async def touch_team_squad(user_id: int) -> None:
    await execute("UPDATE team_squads SET updated_at = NOW() WHERE user_id = $1;", user_id)

async def sync_player_snapshot(player_id: int, updates: dict[str, object]) -> int:
    """Propagate an edited player record into every owned squad snapshot.

    team_squads stores denormalized player dictionaries, so editing the global
    or special source row alone does not update an already-purchased snapshot.
    This helper updates matching player_id entries in-place for every squad.
    """
    import json

    clean = {k: v for k, v in (updates or {}).items() if v is not None}
    if not clean:
        return 0
    payload = json.dumps(clean, default=str)
    result = await execute(
        """
        UPDATE team_squads ts
        SET squad = COALESCE((
            SELECT jsonb_agg(
                CASE
                    WHEN elem->>'player_id' = $1::text THEN elem || $2::jsonb
                    ELSE elem
                END
                ORDER BY ord
            )
            FROM jsonb_array_elements(ts.squad) WITH ORDINALITY AS x(elem, ord)
        ), '[]'::jsonb),
            updated_at = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(ts.squad) AS x(elem)
            WHERE elem->>'player_id' = $1::text
        );
        """,
        str(int(player_id)), payload,
    )
    try:
        return int(str(result).split()[-1])
    except Exception:
        return 0

