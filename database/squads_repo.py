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


async def refresh_all_team_squads() -> tuple[int, int]:
    """Synchronize every denormalized squad player snapshot from the
    authoritative global/special player tables.

    Positive player_id values refer to players.player_id. Negative values use
    the special-edition namespace and map to abs(player_id)=special_player_id.
    Other squad metadata is preserved.
    """
    result = await execute(
        """
        WITH refreshed AS (
            SELECT
                ts.user_id,
                COALESCE(
                    jsonb_agg(
                        CASE
                            WHEN ids.player_id > 0 AND gp.player_id IS NOT NULL THEN
                                x.elem || jsonb_build_object(
                                    'player_id', gp.player_id,
                                    'name', gp.name,
                                    'country', gp.country,
                                    'role', gp.role,
                                    'bat_level', gp.bat_level,
                                    'bowl_level', gp.bowl_level,
                                    'batting_hand', gp.batting_hand,
                                    'bowling_hand', gp.bowling_hand,
                                    'is_special', false,
                                    'edition', NULL,
                                    'special_edition_id', NULL
                                )
                            WHEN ids.player_id < 0 AND sp.special_player_id IS NOT NULL THEN
                                x.elem || jsonb_build_object(
                                    'player_id', -sp.special_player_id,
                                    'name', sp.name,
                                    'country', sp.country,
                                    'role', sp.role,
                                    'bat_level', sp.bat_level,
                                    'bowl_level', sp.bowl_level,
                                    'batting_hand', sp.batting_hand,
                                    'bowling_hand', sp.bowling_hand,
                                    'is_special', true,
                                    'edition', sp.edition,
                                    'special_edition_id', sp.special_player_id
                                )
                            ELSE x.elem
                        END
                        ORDER BY x.ord
                    ),
                    '[]'::jsonb
                ) AS squad
            FROM team_squads ts
            CROSS JOIN LATERAL jsonb_array_elements(ts.squad) WITH ORDINALITY AS x(elem, ord)
            CROSS JOIN LATERAL (SELECT CASE WHEN (x.elem->>'player_id') ~ '^-?[0-9]+$' THEN (x.elem->>'player_id')::bigint END AS player_id) AS ids
            LEFT JOIN players gp
                ON ids.player_id = gp.player_id
            LEFT JOIN special_edition_players sp
                ON ids.player_id < 0
               AND abs(ids.player_id) = sp.special_player_id
            GROUP BY ts.user_id
        )
        UPDATE team_squads ts
        SET squad = refreshed.squad,
            updated_at = NOW()
        FROM refreshed
        WHERE ts.user_id = refreshed.user_id
        RETURNING ts.user_id, jsonb_array_length(ts.squad) AS player_count;
        """
    )

    # asyncpg returns a command status for execute(). The statement above is
    # intentionally a single atomic UPDATE so users never see half-refreshed
    # squads. If the adapter returns rows in a future runtime, use them for a
    # more precise count; otherwise derive the totals from a follow-up query.
    rows = await fetch("SELECT user_id, jsonb_array_length(squad) AS player_count FROM team_squads;")
    users = len(rows)
    players = sum(int(r["player_count"] or 0) for r in rows)
    return users, players
