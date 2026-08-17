"""
Pure logic helpers for the /play game mode (handlers/play/).
No I/O here on purpose - matches the project's engines/ convention of
keeping game rules separate from Telegram/database plumbing.
"""

from __future__ import annotations

import random

PITCHES = {
    "green": "🌿 GREEN",
    "dry": "🏜️ DRY",
    "dusty": "🌪️ DUSTY",
    "flat": "🛣️ FLAT",
    "hard": "🪨 HARD",
    "even": "⚖️ EVEN",
    "bouncy": "🏀 BOUNCY",
    "slow": "🐢 SLOW",
}


def pitch_label(pitch_code: str) -> str:
    return PITCHES.get(pitch_code, pitch_code.upper())


def flip_coin() -> str:
    """Returns "heads" or "tails" with a 50/50 chance."""
    return random.choice(["heads", "tails"])


def playing_xi(squad: list[dict], size: int = 11) -> list[dict]:
    """Picks up to `size` players from a squad for the Playing XI list.
    If the squad has fewer than `size` players, returns all of them."""
    return list(squad[:size])
