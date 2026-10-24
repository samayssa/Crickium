"""Probability and level-band randomisation tables for player acquisition.

This module is intentionally data-driven so the level distributions can be
changed later without rewriting the command handlers.
"""
from __future__ import annotations

import random
from typing import Any

from database.query import fetch

# Debut: ten explicitly controlled players + one bonus player from the same
# overall debut band.  The controlled slots are exactly what the design calls
# for: 3 low, 4 mid, 3 high, with the 11th slot drawn from 55-85.
DEBUT_LEVEL_TABLE = (
    {"name": "LOW", "min": 55, "max": 65, "count": 3, "weight": 0.30},
    {"name": "MID", "min": 66, "max": 77, "count": 4, "weight": 0.40},
    {"name": "HIGH", "min": 78, "max": 85, "count": 3, "weight": 0.30},
)

DEBUT_BONUS_MIN_LEVEL = 55
DEBUT_BONUS_MAX_LEVEL = 85

# A debut Playing XI uses five database Batsman rows, three AllRounders and
# three Bowlers.  One or two of the five batsmen are marked as wicket-keepers,
# producing 3-4 batsmen + 1-2 wicket-keepers in the displayed XI.
DEBUT_ROLE_TABLE = {
    "Batsman": 5,
    "AllRounder": 3,
    "Bowler": 3,
}

# Claim distribution.  The weights sum to exactly 100%.
CLAIM_LEVEL_TABLE = (
    {"name": "55-65", "min": 55, "max": 65, "weight": 35.0},
    {"name": "66-75", "min": 66, "max": 75, "weight": 30.0},
    {"name": "76-80", "min": 76, "max": 80, "weight": 21.0},
    {"name": "81-84", "min": 81, "max": 84, "weight": 7.0},
    {"name": "85-87", "min": 85, "max": 87, "weight": 4.0},
    {"name": "88-90", "min": 88, "max": 90, "weight": 1.5},
    {"name": "91-93", "min": 91, "max": 93, "weight": 0.9},
    {"name": "94-96", "min": 94, "max": 96, "weight": 0.4},
    {"name": "97-99", "min": 97, "max": 99, "weight": 0.2},
)


def player_level(player: dict[str, Any]) -> int:
    """Return the player level used for rarity/randomisation bands."""
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


def weighted_claim_band() -> dict[str, Any]:
    """Choose one claim level band using the configured percentage weights."""
    bands = [entry for entry in CLAIM_LEVEL_TABLE]
    return random.choices(bands, weights=[entry["weight"] for entry in bands], k=1)[0]


def weighted_debut_bonus_band() -> dict[str, Any]:
    """Choose the 11th debut level band using the same low/mid/high balance."""
    bands = [entry for entry in DEBUT_LEVEL_TABLE]
    return random.choices(bands, weights=[entry["weight"] for entry in bands], k=1)[0]


async def _players_in_level_band(role: str, min_level: int, max_level: int) -> list[dict[str, Any]]:
    """Fetch players of a role whose relevant playing level falls in the band."""
    role = str(role)
    if role == "Batsman":
        level_expr = "bat_level"
    elif role == "Bowler":
        level_expr = "bowl_level"
    else:
        # All-rounders and wicket-keepers can have either a strong batting or
        # bowling level. Debut level is the player's stronger skill.
        level_expr = "GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0))"

    rows = await fetch(
        f"""
        SELECT * FROM players
        WHERE role = $1
          AND {level_expr} BETWEEN $2 AND $3
        ORDER BY random();
        """,
        role,
        int(min_level),
        int(max_level),
    )
    return [dict(row) for row in rows]


# Every valid 11-player composition satisfies the requested ranges:
#   Batsmen: 3-4
#   Wicketkeepers: 1-2
#   All-rounders: 3-4
#   Bowlers: 3-4
# and totals exactly 11 players.
VALID_DEBUT_ROLE_COMPOSITIONS = (
    (4, 1, 3, 3),
    (3, 2, 3, 3),
    (3, 1, 4, 3),
    (3, 1, 3, 4),
)

DEBUT_ROLES = ("Batsman", "Wicketkeeper", "AllRounder", "Bowler")


def _normalise_debut_player(player: dict[str, Any]) -> dict[str, Any]:
    """Add the legacy keeper flag without changing the database role."""
    result = dict(player)
    result["is_wicketkeeper"] = str(result.get("role") or "").lower() == "wicketkeeper"
    return result


async def generate_debut_xi() -> list[dict[str, Any]]:
    """Generate one valid debut XI using the level bands and real DB roles.

    Wicketkeepers are now first-class database rows. The generator therefore
    selects them from role='Wicketkeeper' instead of pretending they are
    Batsman rows, which was the source of the debut-generation failure after
    wicketkeepers were added to the player roster.
    """
    # A few attempts handle uneven real-world rosters where one exact
    # role/band combination may be sparse.
    for _attempt in range(80):
        composition = random.choice(VALID_DEBUT_ROLE_COMPOSITIONS)
        role_counts = dict(zip(DEBUT_ROLES, composition))

        slot_bands: list[dict[str, Any]] = []
        for entry in DEBUT_LEVEL_TABLE:
            slot_bands.extend([entry] * int(entry["count"]))
        slot_bands.append(weighted_debut_bonus_band())
        random.shuffle(slot_bands)

        role_slots: list[str] = []
        for role, count in role_counts.items():
            role_slots.extend([role] * int(count))
        random.shuffle(role_slots)

        pools: list[list[dict[str, Any]]] = []
        failed = False
        for role, band in zip(role_slots, slot_bands):
            pool = await _players_in_level_band(role, band["min"], band["max"])
            if not pool:
                failed = True
                break
            pools.append(pool)
        if failed:
            continue

        # Pick from the most constrained pools first to avoid collisions.
        selected_by_slot: dict[int, dict[str, Any]] = {}
        used_ids: set[int] = set()
        for idx in sorted(range(len(pools)), key=lambda i: len(pools[i])):
            candidates = [
                player for player in pools[idx]
                if int(player.get("player_id") or 0) not in used_ids
            ]
            if not candidates:
                failed = True
                break
            chosen = random.choice(candidates)
            selected_by_slot[idx] = _normalise_debut_player(chosen)
            used_ids.add(int(chosen.get("player_id") or 0))

        if failed or len(selected_by_slot) != 11:
            continue

        selected = [selected_by_slot[i] for i in range(11)]
        # Stable display/game order: batsmen, wicketkeepers, all-rounders,
        # bowlers. This makes /debut and /pxl deterministic after generation.
        order = {"Batsman": 0, "Wicketkeeper": 1, "AllRounder": 2, "Bowler": 3}
        selected.sort(key=lambda p: order.get(str(p.get("role") or ""), 9))
        return selected

    raise ValueError(
        "Not enough players are available across the requested debut level bands "
        "and valid role combinations."
    )


async def get_random_claim_player() -> dict[str, Any] | None:
    """Choose a claim reward from the configured weighted level bands."""
    band = weighted_claim_band()
    rows = await fetch(
        """
        SELECT * FROM players
        WHERE GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2
        ORDER BY random()
        LIMIT 1;
        """,
        int(band["min"]), int(band["max"]),
    )
    return dict(rows[0]) if rows else None
