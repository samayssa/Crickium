
from __future__ import annotations

from database.query import execute

CLEAR_TABLES = [
    "player_claims",
    "team_lineups",
    "match_challenges",
    "matches",
    "player_stats",
    "team_squads",
    "players",
    "users",
    "probability_profiles",
]


async def clear_all_game_data() -> None:
    tables = ", ".join(CLEAR_TABLES)
    await execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE;")
