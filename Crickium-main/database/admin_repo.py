
from __future__ import annotations

from database.query import execute

CLEAR_TABLES = [
    "player_claims",
    "team_lineups",
    "referrals",
    "match_challenges",
    "matches",
    "player_stats",
    "team_squads",
    "players",
    "special_edition_players",
    "showcase_sets",
    "showcase_cards",
    "users",
    "probability_profiles",
]


async def clear_all_game_data() -> None:
    tables = ", ".join(CLEAR_TABLES)
    await execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE;")
    try:
        from services.quest_engine import invalidate_period_cache
        invalidate_period_cache()
        from utils.debut_gate import invalidate_debut_cache
        invalidate_debut_cache()
        from database.broadcast_repo import _RECENT_UPSERTS
        _RECENT_UPSERTS.clear()
    except Exception:
        pass
