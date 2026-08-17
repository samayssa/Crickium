"""
Level & tier system for player profiles.

- 30 levels total, split into 6 tiers of 5 levels each.
- XP needed to reach the next level grows a little every level:
      xp_to_next(level) = 20 * level + 80
- Level 30 is MAX - there is no further "next level", so XP simply caps
  there instead of overflowing.

Match XP rewards (used by handlers/play/live.py and handlers/exitgame_play.py):
- Win  -> WIN_XP
- Loss -> LOSS_XP
- Tie  -> TIE_XP
- Exiting your own match early (/exitgame) -> EXIT_PENALTY_XP (0), the
  opponent who stayed gets WIN_XP as if they had won.
"""
from __future__ import annotations

MAX_LEVEL = 30

# --- Match XP rewards ---
WIN_XP = 50
LOSS_XP = 20
TIE_XP = 35
EXIT_PENALTY_XP = 0  # the player who exits early gets no XP for that match

# --- Tiers: 5 levels each, 6 tiers, covering levels 1-30 ---
# (min_level, max_level, emoji, short_name (also the /upload_img keyword,
# lowercased), full_title)
TIERS = [
    (1, 5, "🥉", "bronze", "Gully Cricketer"),
    (6, 10, "🥈", "silver", "Club Player"),
    (11, 15, "🥇", "gold", "State Player"),
    (16, 20, "💎", "platinum", "National Player"),
    (21, 25, "👑", "diamond", "International Star"),
    (26, 30, "🏆", "legend", "Cricket Icon"),
]

# Ordered tier keys, exactly matching the coordinate-file folder names and
# the /upload_img <tier> keyword.
TIER_KEYS = [t[3] for t in TIERS]


def get_tier(level: int) -> tuple[str, str, str]:
    """Returns (tier_emoji, tier_key, tier_full_title) for a level.
    tier_key is lowercase (bronze/silver/gold/platinum/diamond/legend) -
    the same key used by /upload_img and the coordinates/ folder.
    Clamps out-of-range levels to the nearest valid tier."""
    level = max(1, min(int(level), MAX_LEVEL))
    for min_lvl, max_lvl, emoji, key, title in TIERS:
        if min_lvl <= level <= max_lvl:
            return emoji, key, title
    return TIERS[-1][2], TIERS[-1][3], TIERS[-1][4]


def xp_to_next_level(level: int) -> int | None:
    """XP required to go from `level` to `level + 1`. Returns None at MAX_LEVEL."""
    if level >= MAX_LEVEL:
        return None
    return 20 * level + 80


def add_xp(current_level: int, current_xp: int, xp_gained: int) -> tuple[int, int]:
    """Applies `xp_gained` to a player's (level, xp), rolling over as many
    level-ups as the XP allows. Caps cleanly at MAX_LEVEL (extra XP earned
    once already at MAX_LEVEL is simply discarded)."""
    level = max(1, min(int(current_level), MAX_LEVEL))
    xp = max(0, int(current_xp))

    if level >= MAX_LEVEL:
        return MAX_LEVEL, 0

    xp += max(0, int(xp_gained))

    while level < MAX_LEVEL:
        needed = xp_to_next_level(level)
        if needed is None or xp < needed:
            break
        xp -= needed
        level += 1

    if level >= MAX_LEVEL:
        return MAX_LEVEL, 0

    return level, xp


def progress_bar(level: int, xp: int, *, slots: int = 10) -> str:
    """A small text progress bar toward the next level, e.g. '▓▓▓▓░░░░░░'.
    Returns a plain 'MAX' marker at level 30."""
    needed = xp_to_next_level(level)
    if needed is None or needed <= 0:
        return "MAX"
    filled = max(0, min(slots, round(slots * xp / needed)))
    return "▓" * filled + "░" * (slots - filled)
