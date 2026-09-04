"""Ruby pricing for the player-upgrade system.

Kept in one place so upgrade pricing can be tuned without touching handlers.
"""
from __future__ import annotations

UPGRADE_RUBY_PRICES: dict[int, int] = {
    1: 250,
    2: 500,
    3: 1_000,
    4: 2_000,
}

TIER_STRENGTHS: dict[int, float] = {
    1: 0.05,
    2: 0.075,
    3: 0.10,
    4: 0.15,
}


def upgrade_price(tier: int) -> int:
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        raise ValueError("tier must be 1-4")
    if tier not in UPGRADE_RUBY_PRICES:
        raise ValueError("tier must be 1-4")
    return UPGRADE_RUBY_PRICES[tier]


def tier_strength(tier: int) -> float:
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        raise ValueError("tier must be 1-4")
    if tier not in TIER_STRENGTHS:
        raise ValueError("tier must be 1-4")
    return TIER_STRENGTHS[tier]
