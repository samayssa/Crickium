"""
card_coordinates/ holds one file per tier (bronze.py, silver.py, gold.py,
platinum.py, diamond.py, legend.py), each describing where things sit on
that tier's player card template image.

Use get_coordinates(tier_key) to fetch a tier's layout as a plain dict
instead of importing each tier module by hand.
"""
from __future__ import annotations

from importlib import import_module

from engines.level_engine import TIER_KEYS


def get_coordinates(tier_key: str) -> dict:
    """Returns the coordinate constants for a tier ('bronze', 'silver',
    'gold', 'platinum', 'diamond', or 'legend') as a dict. Falls back to
    'bronze' for an unrecognized key.

    Reads the tier module's current values fresh on every call (no
    caching here) - after editing a card_coordinates/<tier>.py file, a
    normal bot restart is all that's needed for the new values to take
    effect, with nothing extra to clear."""
    key = (tier_key or "").strip().lower()
    if key not in TIER_KEYS:
        key = "bronze"

    mod = import_module(f"card_coordinates.{key}")
    return {
        "TIER_KEY": mod.TIER_KEY,
        "TIER_TITLE": mod.TIER_TITLE,
        "CANVAS_WIDTH": mod.CANVAS_WIDTH,
        "CANVAS_HEIGHT": mod.CANVAS_HEIGHT,
        "PROFILE_CIRCLE": mod.PROFILE_CIRCLE,
        "PLAYER_NAME_BOX": mod.PLAYER_NAME_BOX,
        "TIER_PILL": mod.TIER_PILL,
        "CAPTAIN_ROW": mod.CAPTAIN_ROW,
        "FRANCHISE_ROW": mod.FRANCHISE_ROW,
        "SQUAD_ROW": mod.SQUAD_ROW,
        "CARD_SAFE_ZONE": mod.CARD_SAFE_ZONE,
    }
