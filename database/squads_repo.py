"""Helpers for storing and reading a user's squad from team_squads."""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, transaction


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

    A squad entry's player_id can go dead without the entry ever being
    removed - e.g. a special player gets deleted and then re-added, which
    creates a brand new special_player_id. The old id no longer matches
    anything, so a plain id-based refresh leaves that entry exactly as
    stale as it was. When that happens here, the entry is re-linked by
    name (and edition, for special players) to whatever currently holds
    that name, and that user's historical /plstats rows for the dead id
    are carried over to the new one so past performances aren't stranded
    under an id nothing points to anymore.
    """

    async def _tx(conn):
        # Every squad entry whose stored id no longer resolves, matched by
        # name (+ edition for special players) to whatever currently holds
        # that name. Only entries that actually need to move show up here.
        remap_rows = await conn.fetch(
            """
            SELECT DISTINCT
                ts.user_id,
                ids.player_id AS old_player_id,
                CASE WHEN ids.player_id > 0 THEN gp_by_name.player_id
                     ELSE -sp_by_name.special_player_id END AS new_player_id
            FROM team_squads ts
            CROSS JOIN LATERAL jsonb_array_elements(ts.squad) AS x(elem)
            CROSS JOIN LATERAL (
                SELECT CASE WHEN (x.elem->>'player_id') ~ '^-?[0-9]+$'
                            THEN (x.elem->>'player_id')::bigint END AS player_id
            ) AS ids
            LEFT JOIN players gp
                ON ids.player_id > 0 AND ids.player_id = gp.player_id
            LEFT JOIN players gp_by_name
                ON ids.player_id > 0 AND gp.player_id IS NULL
               AND LOWER(gp_by_name.name) = LOWER(x.elem->>'name')
            LEFT JOIN special_edition_players sp
                ON ids.player_id < 0 AND abs(ids.player_id) = sp.special_player_id
            LEFT JOIN special_edition_players sp_by_name
                ON ids.player_id < 0 AND sp.special_player_id IS NULL
               AND LOWER(sp_by_name.name) = LOWER(x.elem->>'name')
               AND LOWER(sp_by_name.edition) = LOWER(COALESCE(x.elem->>'edition', ''))
            WHERE ids.player_id IS NOT NULL
              AND ((ids.player_id > 0 AND gp.player_id IS NULL AND gp_by_name.player_id IS NOT NULL)
                OR (ids.player_id < 0 AND sp.special_player_id IS NULL AND sp_by_name.special_player_id IS NOT NULL));
            """
        )

        await conn.execute(
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
                                WHEN ids.player_id > 0 AND gp.player_id IS NULL AND gp_by_name.player_id IS NOT NULL THEN
                                    x.elem || jsonb_build_object(
                                        'player_id', gp_by_name.player_id,
                                        'name', gp_by_name.name,
                                        'country', gp_by_name.country,
                                        'role', gp_by_name.role,
                                        'bat_level', gp_by_name.bat_level,
                                        'bowl_level', gp_by_name.bowl_level,
                                        'batting_hand', gp_by_name.batting_hand,
                                        'bowling_hand', gp_by_name.bowling_hand,
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
                                WHEN ids.player_id < 0 AND sp.special_player_id IS NULL AND sp_by_name.special_player_id IS NOT NULL THEN
                                    x.elem || jsonb_build_object(
                                        'player_id', -sp_by_name.special_player_id,
                                        'name', sp_by_name.name,
                                        'country', sp_by_name.country,
                                        'role', sp_by_name.role,
                                        'bat_level', sp_by_name.bat_level,
                                        'bowl_level', sp_by_name.bowl_level,
                                        'batting_hand', sp_by_name.batting_hand,
                                        'bowling_hand', sp_by_name.bowling_hand,
                                        'is_special', true,
                                        'edition', sp_by_name.edition,
                                        'special_edition_id', sp_by_name.special_player_id
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
                    ON ids.player_id > 0 AND ids.player_id = gp.player_id
                LEFT JOIN players gp_by_name
                    ON ids.player_id > 0 AND gp.player_id IS NULL
                   AND LOWER(gp_by_name.name) = LOWER(x.elem->>'name')
                LEFT JOIN special_edition_players sp
                    ON ids.player_id < 0
                   AND abs(ids.player_id) = sp.special_player_id
                LEFT JOIN special_edition_players sp_by_name
                    ON ids.player_id < 0 AND sp.special_player_id IS NULL
                   AND LOWER(sp_by_name.name) = LOWER(x.elem->>'name')
                   AND LOWER(sp_by_name.edition) = LOWER(COALESCE(x.elem->>'edition', ''))
                GROUP BY ts.user_id
            )
            UPDATE team_squads ts
            SET squad = refreshed.squad,
                updated_at = NOW()
            FROM refreshed
            WHERE ts.user_id = refreshed.user_id;
            """
        )

        # Carry that user's historical /plstats ledger over to the new id so a
        # deleted-then-recreated player doesn't reset to zero after a refresh.
        # Scoped to (user_id, old_id) -> (user_id, new_id) only, and skips any
        # match_id the new id already has a row for, so the unique
        # (match_id, user_id, player_id) constraint can never be violated.
        for row in remap_rows:
            await conn.execute(
                """
                UPDATE player_user_match_stats AS old_row
                SET player_id = $3
                WHERE old_row.user_id = $1 AND old_row.player_id = $2
                  AND NOT EXISTS (
                      SELECT 1 FROM player_user_match_stats new_row
                      WHERE new_row.match_id = old_row.match_id
                        AND new_row.user_id = old_row.user_id
                        AND new_row.player_id = $3
                  );
                """,
                int(row["user_id"]), int(row["old_player_id"]), int(row["new_player_id"]),
            )
            # Any leftover old-id rows here were true duplicates of a match the
            # new id already had a row for (rare) - drop them so no orphaned
            # stats sit under a name/edition that no longer exists.
            await conn.execute(
                "DELETE FROM player_user_match_stats WHERE user_id = $1 AND player_id = $2;",
                int(row["user_id"]), int(row["old_player_id"]),
            )

    await transaction(_tx)

    rows = await fetch("SELECT user_id, jsonb_array_length(squad) AS player_count FROM team_squads;")
    users = len(rows)
    players = sum(int(r["player_count"] or 0) for r in rows)
    return users, players
