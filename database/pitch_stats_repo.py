from database.query import fetch, fetchrow, transaction

COLUMNS = ("n", "runs", "wickets", "detail_n", "pace_wkts", "spin_wkts", "pp_runs", "pp_wkts", "mid_runs", "mid_wkts", "death_runs", "death_wkts")


async def record_match(source: str, match_id: int, pitch: str, innings: dict[int, dict], winner_side: str | None) -> bool:
    async def _tx(conn):
        fresh = await conn.fetchval(
            "INSERT INTO pitch_recorded(source, match_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING match_id;",
            source, int(match_id),
        )
        if fresh is None:
            return False
        for number, row in innings.items():
            values = [int(row.get(column) or 0) for column in COLUMNS]
            await conn.execute(
                """
                INSERT INTO pitch_innings_stats(pitch, innings, n, runs, wickets, detail_n, pace_wkts, spin_wkts,
                    pp_runs, pp_wkts, mid_runs, mid_wkts, death_runs, death_wkts)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                ON CONFLICT (pitch, innings) DO UPDATE SET
                    n = pitch_innings_stats.n + EXCLUDED.n,
                    runs = pitch_innings_stats.runs + EXCLUDED.runs,
                    wickets = pitch_innings_stats.wickets + EXCLUDED.wickets,
                    detail_n = pitch_innings_stats.detail_n + EXCLUDED.detail_n,
                    pace_wkts = pitch_innings_stats.pace_wkts + EXCLUDED.pace_wkts,
                    spin_wkts = pitch_innings_stats.spin_wkts + EXCLUDED.spin_wkts,
                    pp_runs = pitch_innings_stats.pp_runs + EXCLUDED.pp_runs,
                    pp_wkts = pitch_innings_stats.pp_wkts + EXCLUDED.pp_wkts,
                    mid_runs = pitch_innings_stats.mid_runs + EXCLUDED.mid_runs,
                    mid_wkts = pitch_innings_stats.mid_wkts + EXCLUDED.mid_wkts,
                    death_runs = pitch_innings_stats.death_runs + EXCLUDED.death_runs,
                    death_wkts = pitch_innings_stats.death_wkts + EXCLUDED.death_wkts;
                """,
                pitch, int(number), *values,
            )
        defend = 1 if winner_side == "defend" else 0
        chase = 1 if winner_side == "chase" else 0
        await conn.execute(
            """
            INSERT INTO pitch_results(pitch, matches, defend_wins, chase_wins) VALUES($1,$2,$3,$4)
            ON CONFLICT (pitch) DO UPDATE SET
                matches = pitch_results.matches + EXCLUDED.matches,
                defend_wins = pitch_results.defend_wins + EXCLUDED.defend_wins,
                chase_wins = pitch_results.chase_wins + EXCLUDED.chase_wins;
            """,
            pitch, 1 if (defend or chase) else 0, defend, chase,
        )
        return True

    return await transaction(_tx)


async def load_pitch(pitch: str) -> tuple[dict[int, dict], dict]:
    rows = await fetch("SELECT * FROM pitch_innings_stats WHERE pitch=$1;", pitch)
    result = await fetchrow("SELECT matches, defend_wins, chase_wins FROM pitch_results WHERE pitch=$1;", pitch)
    return {int(r["innings"]): dict(r) for r in rows}, dict(result) if result else {"matches": 0, "defend_wins": 0, "chase_wins": 0}
