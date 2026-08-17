"""
Maps a player's overall level (OVR = the higher of bat_level and bowl_level,
see services.player_card.overall_rating) to a rarity tier.

LEVEL RANGE | RARITY
55 - 64  -> Common
65 - 74  -> Medium
75 - 84  -> Rare
85 - 89  -> Epic
90 - 94  -> Elite
95 - 97  -> Legendary
98 - 99  -> Iconic

Only shown in the /player lookup response - not in /claim.
"""

from __future__ import annotations

# (min_level, max_level, rarity_name) - checked in order.
RARITY_TIERS: list[tuple[int, int, str]] = [
    (55, 64, "Common"),
    (65, 74, "Medium"),
    (75, 84, "Rare"),
    (85, 89, "Epic"),
    (90, 94, "Elite"),
    (95, 97, "Legendary"),
    (98, 99, "Iconic"),
]


def get_rarity(level: int) -> str:
    """
    Returns the rarity name for a given overall level. The chart only
    defines 55-99, so anything below is treated as the lowest tier
    (Common) and anything above as the highest tier (Iconic).
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        return RARITY_TIERS[0][2]

    if level < RARITY_TIERS[0][0]:
        return RARITY_TIERS[0][2]
    if level > RARITY_TIERS[-1][1]:
        return RARITY_TIERS[-1][2]

    for low, high, name in RARITY_TIERS:
        if low <= level <= high:
            return name

    return RARITY_TIERS[0][2]
