"""
Database operations backing the level/XP system and the /profile command:
- add_match_xp(): applies match XP to a user and resolves level-ups
- record_match_result(): bumps player_stats (matches/wins/losses)
- get_profile_snapshot(): everything /profile needs in one call
- get_global_rank(): a user's position on the level/xp leaderboard
- ensure_franchise_name(): lazily assigns a default franchise name
"""
from __future__ import annotations

from database.query import execute, fetchrow, fetchval
from engines.level_engine import add_xp


async def add_match_xp(user_id: int, xp_gained: int) -> tuple[int, int]:
    """Applies xp_gained to a user's level/xp (rolling over level-ups as
    needed) and persists the result. Returns the new (level, xp)."""
    row = await fetchrow("SELECT level, xp FROM users WHERE user_id = $1;", user_id)
    current_level = row["level"] if row else 1
    current_xp = row["xp"] if row else 0

    new_level, new_xp = add_xp(current_level, current_xp, xp_gained)

    await execute(
        "UPDATE users SET level = $1, xp = $2 WHERE user_id = $3;",
        new_level, new_xp, user_id,
    )
    print(f"[user_stats_repo] user_id={user_id} +{xp_gained} XP -> level={new_level}, xp={new_xp}")
    return new_level, new_xp


async def record_match_result(user_id: int, *, won: bool | None) -> None:
    """Upserts player_stats for a finished match.
    won=True -> win, won=False -> loss, won=None -> tie (matches only)."""
    win_inc = 1 if won is True else 0
    loss_inc = 1 if won is False else 0

    await execute(
        """
        INSERT INTO player_stats (user_id, matches, wins, losses)
        VALUES ($1, 1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE SET
            matches = player_stats.matches + 1,
            wins = player_stats.wins + $2,
            losses = player_stats.losses + $3;
        """,
        user_id, win_inc, loss_inc,
    )


async def ensure_franchise_name(user_id: int, first_name: str | None) -> str:
    """Returns the user's franchise name, assigning a simple default the
    first time /profile is opened if one hasn't been set yet."""
    current = await fetchval("SELECT franchise_name FROM users WHERE user_id = $1;", user_id)
    if current:
        return current

    default_name = f"{(first_name or 'Player').strip()} XI"
    await execute(
        "UPDATE users SET franchise_name = COALESCE(franchise_name, $1) WHERE user_id = $2;",
        default_name, user_id,
    )
    return default_name


async def get_global_rank(user_id: int) -> int:
    """1-based rank among all users, ordered by level desc then xp desc."""
    rank = await fetchval(
        """
        SELECT rank FROM (
            SELECT user_id, ROW_NUMBER() OVER (ORDER BY level DESC, xp DESC, user_id ASC) AS rank
            FROM users
        ) ranked
        WHERE user_id = $1;
        """,
        user_id,
    )
    return int(rank) if rank else 0


async def get_profile_snapshot(user_id: int) -> dict:
    """Everything /profile needs about the player's level/xp and match record."""
    user_row = await fetchrow(
        "SELECT level, xp, franchise_name FROM users WHERE user_id = $1;", user_id,
    )
    stats_row = await fetchrow(
        "SELECT matches, wins, losses FROM player_stats WHERE user_id = $1;", user_id,
    )

    level = user_row["level"] if user_row else 1
    xp = user_row["xp"] if user_row else 0
    matches = stats_row["matches"] if stats_row else 0
    wins = stats_row["wins"] if stats_row else 0
    win_pct = round((wins / matches) * 100, 1) if matches else 0.0

    return {
        "level": level,
        "xp": xp,
        "matches": matches,
        "wins": wins,
        "win_pct": win_pct,
    }
