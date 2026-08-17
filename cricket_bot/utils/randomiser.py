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
    {"name": "65", "min": 65, "max": 65, "weight": 35.0},
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
    if role == "Batsman":
        level_expr = "bat_level"
    elif role == "Bowler":
        level_expr = "bowl_level"
    else:
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


async def generate_debut_xi() -> list[dict[str, Any]]:
    """Generate one deterministic-shape debut XI under the level constraints."""
    selected: list[dict[str, Any]] = []
    used_ids: set[int] = set()

    # First allocate the exact requested 3 / 4 / 3 level distribution.
    slot_bands = []
    for entry in DEBUT_LEVEL_TABLE:
        slot_bands.extend([entry] * int(entry["count"]))
    slot_bands.append(weighted_debut_bonus_band())
    random.shuffle(slot_bands)

    # Spread the role mix across all 11 slots.  Prefer the requested 5/3/3
    # structure, while randomising which level band receives each role.
    role_slots = []
    for role, count in DEBUT_ROLE_TABLE.items():
        role_slots.extend([role] * int(count))
    random.shuffle(role_slots)

    # Backtracking with a few passes is enough for normal player databases.
    # We require every generated player to satisfy the selected level band.
    pools: list[list[dict[str, Any]]] = []
    for role, band in zip(role_slots, slot_bands):
        pool = await _players_in_level_band(role, band["min"], band["max"])
        pools.append(pool)

    if any(not pool for pool in pools):
        raise ValueError("Not enough players available across the requested debut level/role bands.")

    # Greedy selection with retry ordering: select the smallest pools first to
    # reduce the chance of consuming a unique player needed by another slot.
    indexed = sorted(range(len(pools)), key=lambda i: len(pools[i]))
    chosen: dict[int, dict[str, Any]] = {}
    for idx in indexed:
        candidates = [p for p in pools[idx] if int(p.get("player_id") or 0) not in used_ids]
        if not candidates:
            raise ValueError("Could not build a unique debut XI from the configured pools.")
        player = random.choice(candidates)
        chosen[idx] = player
        used_ids.add(int(player.get("player_id") or 0))

    selected = [chosen[i] for i in range(len(pools))]

    # Mark one or two batsmen as wicket-keepers.  The marker is stored with the
    # squad entry and is respected later by /pxl and game validation.
    batsmen = [p for p in selected if p.get("role") == "Batsman"]
    keeper_count = random.choice((1, 2))
    keeper_count = min(keeper_count, max(1, len(batsmen) - 3))
    keeper_ids = {int(p.get("player_id") or 0) for p in random.sample(batsmen, keeper_count)}
    for player in selected:
        player["is_wicketkeeper"] = int(player.get("player_id") or 0) in keeper_ids

    # Keep a predictable role order for the saved squad and initial XI.
    selected.sort(key=lambda p: {"Batsman": 0, "AllRounder": 1, "Bowler": 2}.get(p.get("role"), 9))
    return selected


async def get_random_claim_player() -> dict[str, Any] | None:
    """Choose a claim reward from the configured weighted level bands."""
    band = weighted_claim_band()

    if band["min"] == band["max"]:
        rows = await _players_in_level_band("Batsman", band["min"], band["max"])
        # A claim can be any role, so query all roles for this level instead.
        rows = await fetch(
            """
            SELECT * FROM players
            WHERE GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2
            ORDER BY random()
            LIMIT 1;
            """,
            int(band["min"]), int(band["max"]),
        )
    else:
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
