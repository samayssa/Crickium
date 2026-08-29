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

    A live player is always refreshed from its current authoritative row, so
    edits to name, levels, role, country, hands, edition, etc. flow into every
    owned squad. A dead global player is removed rather than being silently
    rebound to a newly-created global record with the same name.

    A dead special-edition player is allowed one intentional recovery path for
    delete-then-recreate workflows: when exactly one current special player has
    the same base name and the same non-edition player details (levels, role,
    country, and batting/bowling hands), the old squad entry is swapped to the
    new special_player_id and its historical /plstats ledger follows the swap.
    Otherwise the dead special entry is removed from the squad.
    """

    async def _tx(conn):
        # Special editions use negative squad IDs. If an old special row was
        # deleted and re-uploaded with a new ID, identify one unambiguous
        # replacement by matching everything except the edition itself.
        remap_rows = await conn.fetch(
            """
            SELECT DISTINCT
                ts.user_id,
                ids.player_id AS old_player_id,
                -sp_match.special_player_id AS new_player_id
            FROM team_squads ts
            CROSS JOIN LATERAL jsonb_array_elements(ts.squad) AS x(elem)
            CROSS JOIN LATERAL (
                SELECT CASE WHEN (x.elem->>'player_id') ~ '^-?[0-9]+$'
                            THEN (x.elem->>'player_id')::bigint END AS player_id
            ) AS ids
            LEFT JOIN special_edition_players sp
                ON ids.player_id < 0
               AND abs(ids.player_id) = sp.special_player_id
            LEFT JOIN LATERAL (
                SELECT
                    MIN(candidate.special_player_id) AS special_player_id,
                    COUNT(*) AS candidate_count
                FROM special_edition_players candidate
                WHERE ids.player_id < 0
                  AND sp.special_player_id IS NULL
                  AND LOWER(candidate.name) = LOWER(x.elem->>'name')
                  AND candidate.bat_level = NULLIF(x.elem->>'bat_level', '')::integer
                  AND candidate.bowl_level = NULLIF(x.elem->>'bowl_level', '')::integer
                  AND LOWER(COALESCE(candidate.country, '')) = LOWER(COALESCE(x.elem->>'country', ''))
                  AND LOWER(COALESCE(candidate.role, '')) = LOWER(COALESCE(x.elem->>'role', ''))
                  AND LOWER(COALESCE(candidate.batting_hand, '')) = LOWER(COALESCE(x.elem->>'batting_hand', ''))
                  AND LOWER(COALESCE(candidate.bowling_hand, '')) = LOWER(COALESCE(x.elem->>'bowling_hand', ''))
            ) AS sp_match ON TRUE
            WHERE ids.player_id < 0
              AND sp.special_player_id IS NULL
              AND sp_match.candidate_count = 1;
            """
        )

        # Keep a second list of all dead entries that have no safe replacement.
        # Those player IDs must disappear from both the squad and the personal
        # stats ledger, otherwise /sell or other ownership-aware flows can see
        # a stale card forever.
        removed_rows = await conn.fetch(
            """
            SELECT DISTINCT
                ts.user_id,
                ids.player_id AS old_player_id
            FROM team_squads ts
            CROSS JOIN LATERAL jsonb_array_elements(ts.squad) AS x(elem)
            CROSS JOIN LATERAL (
                SELECT CASE WHEN (x.elem->>'player_id') ~ '^-?[0-9]+$'
                            THEN (x.elem->>'player_id')::bigint END AS player_id
            ) AS ids
            LEFT JOIN players gp
                ON ids.player_id > 0 AND ids.player_id = gp.player_id
            LEFT JOIN special_edition_players sp
                ON ids.player_id < 0 AND abs(ids.player_id) = sp.special_player_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS candidate_count
                FROM special_edition_players candidate
                WHERE ids.player_id < 0
                  AND sp.special_player_id IS NULL
                  AND LOWER(candidate.name) = LOWER(x.elem->>'name')
                  AND candidate.bat_level = NULLIF(x.elem->>'bat_level', '')::integer
                  AND candidate.bowl_level = NULLIF(x.elem->>'bowl_level', '')::integer
                  AND LOWER(COALESCE(candidate.country, '')) = LOWER(COALESCE(x.elem->>'country', ''))
                  AND LOWER(COALESCE(candidate.role, '')) = LOWER(COALESCE(x.elem->>'role', ''))
                  AND LOWER(COALESCE(candidate.batting_hand, '')) = LOWER(COALESCE(x.elem->>'batting_hand', ''))
                  AND LOWER(COALESCE(candidate.bowling_hand, '')) = LOWER(COALESCE(x.elem->>'bowling_hand', ''))
            ) AS sp_match ON TRUE
            WHERE ids.player_id IS NOT NULL
              AND (
                    (ids.player_id > 0 AND gp.player_id IS NULL)
                 OR (ids.player_id < 0 AND sp.special_player_id IS NULL AND sp_match.candidate_count <> 1)
              );
            """
        )

        # Build every user's refreshed squad from authoritative DB rows. A
        # dead/replaced entry is omitted when no safe match exists.
        await conn.execute(
            """
            WITH element_rows AS (
                SELECT
                    ts.user_id,
                    x.ord,
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
                        WHEN ids.player_id < 0 AND sp.special_player_id IS NULL
                             AND sp_match.candidate_count = 1 THEN
                            x.elem || jsonb_build_object(
                                'player_id', -sp_match.special_player_id,
                                'name', sp_match.name,
                                'country', sp_match.country,
                                'role', sp_match.role,
                                'bat_level', sp_match.bat_level,
                                'bowl_level', sp_match.bowl_level,
                                'batting_hand', sp_match.batting_hand,
                                'bowling_hand', sp_match.bowling_hand,
                                'is_special', true,
                                'edition', sp_match.edition,
                                'special_edition_id', sp_match.special_player_id
                            )
                        ELSE NULL
                    END AS refreshed_elem
                FROM team_squads ts
                CROSS JOIN LATERAL jsonb_array_elements(ts.squad) WITH ORDINALITY AS x(elem, ord)
                CROSS JOIN LATERAL (
                    SELECT CASE WHEN (x.elem->>'player_id') ~ '^-?[0-9]+$'
                                THEN (x.elem->>'player_id')::bigint END AS player_id
                ) AS ids
                LEFT JOIN players gp
                    ON ids.player_id > 0 AND ids.player_id = gp.player_id
                LEFT JOIN special_edition_players sp
                    ON ids.player_id < 0 AND abs(ids.player_id) = sp.special_player_id
                LEFT JOIN LATERAL (
                    SELECT
                        MIN(candidate.special_player_id) AS special_player_id,
                        COUNT(*) AS candidate_count,
                        MIN(candidate.name) AS name,
                        MIN(candidate.country) AS country,
                        MIN(candidate.role) AS role,
                        MIN(candidate.bat_level) AS bat_level,
                        MIN(candidate.bowl_level) AS bowl_level,
                        MIN(candidate.batting_hand) AS batting_hand,
                        MIN(candidate.bowling_hand) AS bowling_hand,
                        MIN(candidate.edition) AS edition
                    FROM special_edition_players candidate
                    WHERE ids.player_id < 0
                      AND sp.special_player_id IS NULL
                      AND LOWER(candidate.name) = LOWER(x.elem->>'name')
                      AND candidate.bat_level = NULLIF(x.elem->>'bat_level', '')::integer
                      AND candidate.bowl_level = NULLIF(x.elem->>'bowl_level', '')::integer
                      AND LOWER(COALESCE(candidate.country, '')) = LOWER(COALESCE(x.elem->>'country', ''))
                      AND LOWER(COALESCE(candidate.role, '')) = LOWER(COALESCE(x.elem->>'role', ''))
                      AND LOWER(COALESCE(candidate.batting_hand, '')) = LOWER(COALESCE(x.elem->>'batting_hand', ''))
                      AND LOWER(COALESCE(candidate.bowling_hand, '')) = LOWER(COALESCE(x.elem->>'bowling_hand', ''))
                ) AS sp_match ON TRUE
            )
            , refreshed AS (
                SELECT
                    user_id,
                    COALESCE(
                        jsonb_agg(refreshed_elem ORDER BY ord) FILTER (WHERE refreshed_elem IS NOT NULL),
                        '[]'::jsonb
                    ) AS squad
                FROM element_rows
                GROUP BY user_id
            )
            UPDATE team_squads ts
            SET squad = refreshed.squad,
                updated_at = NOW()
            FROM refreshed
            WHERE ts.user_id = refreshed.user_id;
            """
        )

        # Carry historical /plstats rows across an unambiguous special-edition
        # delete/recreate swap. Global deletions are never rebound by name.
        for row in remap_rows:
            await conn.execute(
                """
                UPDATE player_user_match_stats AS old_row
                SET player_id = $3
                WHERE old_row.user_id = $1 AND old_row.player_id = $2
                  AND NOT EXISTS (
                      SELECT 1
                      FROM player_user_match_stats new_row
                      WHERE new_row.match_id = old_row.match_id
                        AND new_row.user_id = old_row.user_id
                        AND new_row.player_id = $3
                  );
                """,
                int(row["user_id"]), int(row["old_player_id"]), int(row["new_player_id"]),
            )
            await conn.execute(
                "DELETE FROM player_user_match_stats WHERE user_id = $1 AND player_id = $2;",
                int(row["user_id"]), int(row["old_player_id"]),
            )

        # Remove stats belonging to dead global/special players that were not
        # safely remapped into a current special edition.
        for row in removed_rows:
            await conn.execute(
                "DELETE FROM player_user_match_stats WHERE user_id = $1 AND player_id = $2;",
                int(row["user_id"]), int(row["old_player_id"]),
            )

    await transaction(_tx)

    rows = await fetch("SELECT user_id, jsonb_array_length(squad) AS player_count FROM team_squads;")
    users = len(rows)
    players = sum(int(r["player_count"] or 0) for r in rows)
    return users, players
