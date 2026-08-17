from __future__ import annotations

from database.query import fetchrow
from database.squads_repo import get_team_squad
from engines.lineup_engine import load_current_xi

MIN_PLAYERS_FOR_GAME = 11


async def has_completed_debut(user_id: int) -> bool:
    row = await fetchrow("SELECT 1 FROM team_squads WHERE user_id = $1 LIMIT 1;", user_id)
    return row is not None


async def squad_size(user_id: int) -> int:
    squad = await get_team_squad(user_id) or []
    return len(squad)


async def has_minimum_team(user_id: int, minimum: int = MIN_PLAYERS_FOR_GAME) -> bool:
    return (await squad_size(user_id)) >= minimum


def validate_playing_xi(xi: list[dict]) -> tuple[bool, str]:
    """Validate the requested 11-player composition.

    The data model stores wicket-keepers as a flag on a Batsman row. A valid XI
    must therefore contain 3-4 normal batsmen, 1-2 keepers, 3-4 all-rounders and
    3-4 bowlers, totaling exactly eleven players.
    """
    if len(xi) != 11:
        return False, "11 players are required in your Playing XI."

    raw_batsmen = [p for p in xi if p.get("role") == "Batsman"]
    flagged_keepers = [p for p in raw_batsmen if p.get("is_wicketkeeper")]
    # Backward compatibility for squads created before wicket-keeper metadata
    # was introduced: the fifth Batsman row is the historical keeper slot.
    if not flagged_keepers and len(raw_batsmen) == 5:
        raw_batsmen[-1]["is_wicketkeeper"] = True
        flagged_keepers = [raw_batsmen[-1]]
    keepers = flagged_keepers
    keeper_ids = {int(p.get("player_id") or 0) for p in keepers}
    batsmen = [p for p in raw_batsmen if int(p.get("player_id") or 0) not in keeper_ids]
    allrounders = [p for p in xi if p.get("role") == "AllRounder"]
    bowlers = [p for p in xi if p.get("role") == "Bowler"]

    if not (3 <= len(batsmen) <= 4):
        return False, f"You need 3-4 batsmen (excluding wicket-keepers); you have {len(batsmen)}."
    if not (1 <= len(keepers) <= 2):
        return False, f"You need 1-2 wicket-keepers; you have {len(keepers)}."
    if not (3 <= len(allrounders) <= 4):
        return False, f"You need 3-4 all-rounders; you have {len(allrounders)}."
    if not (3 <= len(bowlers) <= 4):
        return False, f"You need 3-4 bowlers; you have {len(bowlers)}."

    return True, ""


async def get_playing_xi_status(user_id: int) -> tuple[bool, str]:
    xi = await load_current_xi(user_id) or []
    return validate_playing_xi(xi[:11])


async def has_perfect_playing_xi(user_id: int) -> bool:
    valid, _ = await get_playing_xi_status(user_id)
    return valid
