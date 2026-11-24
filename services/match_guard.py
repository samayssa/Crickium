from database import play_repo, playint_repo, playipl_repo, playso_repo, playwpl_repo
from database.query import fetchval

SELL_BLOCKED_TEXT = "You can't sell players while you are in a match. Finish or end your match first, then try again."

_LOOKUPS = (
    play_repo.get_active_match_for_user,
    playint_repo.get_active_match_for_user,
    playipl_repo.get_active_match_for_user,
    playwpl_repo.get_active_match_for_user,
    playso_repo.get_active_match_for_user,
)


AI_GAME_IDLE_MINUTES = 30


async def _ai_game_running(user_id: int) -> bool:
    return bool(await fetchval(
        "SELECT 1 FROM miniapp_game_sessions WHERE user_id=$1 AND status='active' "
        "AND updated_at > NOW() - ($2::int * INTERVAL '1 minute') LIMIT 1;",
        int(user_id), AI_GAME_IDLE_MINUTES,
    ))


async def user_in_match(user_id: int) -> bool:
    uid = int(user_id)
    for lookup in (*_LOOKUPS, _ai_game_running):
        try:
            if await lookup(uid):
                return True
        except Exception as exc:
            print(f"[match_guard] lookup failed for {getattr(lookup, '__module__', '?')}: {exc!r}")
            return True
    return False
