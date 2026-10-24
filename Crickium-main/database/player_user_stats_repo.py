from __future__ import annotations

from collections import defaultdict
from database.query import execute, fetchrow


async def reset_player_user_stats(user_id: int, player_id: int) -> None:
    """Start a fresh stats ledger for this user's new ownership of a player."""
    await execute(
        "DELETE FROM player_user_match_stats WHERE user_id = $1 AND player_id = $2;",
        int(user_id), int(player_id),
    )


async def record_match_player_stats(match_id: int, user_id: int, player_rows: list[dict]) -> None:
    """Insert one per-match ledger row for every player in the user's XI."""
    if not player_rows:
        return
    rows = []
    for row in player_rows:
        rows.append((
            int(match_id), int(user_id), int(row['player_id']),
            int(row.get('bat_matches', 1) or 0), int(row.get('bat_innings', 0) or 0),
            int(row.get('runs', 0) or 0), int(row.get('fifties', 0) or 0), int(row.get('centuries', 0) or 0),
            int(row.get('bat_balls', 0) or 0), int(row.get('dismissals', 0) or 0),
            int(row.get('bowl_matches', 1) or 0), int(row.get('bowl_innings', 0) or 0),
            int(row.get('wickets', 0) or 0), int(row.get('three_wickets', 0) or 0), int(row.get('five_wickets', 0) or 0),
            int(row.get('bowl_balls', 0) or 0), int(row.get('bowl_runs', 0) or 0),
        ))
    from database.query import transaction

    async def _tx(conn):
        await conn.executemany(
            """
            INSERT INTO player_user_match_stats (
                match_id, user_id, player_id,
                bat_matches, bat_innings, runs, fifties, centuries, bat_balls, dismissals,
                bowl_matches, bowl_innings, wickets, three_wickets, five_wickets, bowl_balls, bowl_runs
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17
            )
            ON CONFLICT (match_id, user_id, player_id) DO NOTHING;
            """,
            rows,
        )
    await transaction(_tx)


async def get_player_user_stats(user_id: int, player_id: int) -> dict:
    row = await fetchrow(
        """
        SELECT
            COALESCE(SUM(bat_matches),0) AS bat_matches,
            COALESCE(SUM(bat_innings),0) AS bat_innings,
            COALESCE(SUM(runs),0) AS runs,
            COALESCE(SUM(fifties),0) AS fifties,
            COALESCE(SUM(centuries),0) AS centuries,
            COALESCE(SUM(bat_balls),0) AS bat_balls,
            COALESCE(SUM(dismissals),0) AS dismissals,
            COALESCE(SUM(bowl_matches),0) AS bowl_matches,
            COALESCE(SUM(bowl_innings),0) AS bowl_innings,
            COALESCE(SUM(wickets),0) AS wickets,
            COALESCE(SUM(three_wickets),0) AS three_wickets,
            COALESCE(SUM(five_wickets),0) AS five_wickets,
            COALESCE(SUM(bowl_balls),0) AS bowl_balls,
            COALESCE(SUM(bowl_runs),0) AS bowl_runs
        FROM player_user_match_stats
        WHERE user_id = $1 AND player_id = $2;
        """,
        int(user_id), int(player_id),
    )
    return dict(row) if row else {}
