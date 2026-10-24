"""IPL overseas-player classification helpers.

The player ``country`` value is the single source of truth. India is treated as
non-overseas; every other explicitly supplied country is overseas. Missing or
blank country values are treated as unknown and therefore do not consume an
overseas slot until the source data identifies a country.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

MAX_OVERSEAS_PLAYERS = 4

# Keep common country/code spellings accepted by existing uploads while still
# making the rule intentionally narrow: only India is non-overseas.
_INDIA_ALIASES = {
    "india",
    "indian",
    "ind",
    "in",
    "bharat",
    "republic of india",
    "भारत",
    "🇮🇳",
}


def normalize_country(country: Any) -> str:
    """Return a stable, case-insensitive country token for classification."""
    value = unicodedata.normalize("NFKC", str(country or "")).strip().casefold()
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def is_indian_country(country: Any) -> bool:
    """Whether the supplied country represents India."""
    return normalize_country(country) in _INDIA_ALIASES


def is_overseas_country(country: Any) -> bool:
    """Whether an explicitly supplied country should consume an IPL overseas slot."""
    normalized = normalize_country(country)
    return bool(normalized) and normalized not in _INDIA_ALIASES


def classify_country(country: Any) -> str:
    """Return ``indian``, ``overseas`` or ``unknown`` for a player country."""
    normalized = normalize_country(country)
    if not normalized:
        return "unknown"
    return "indian" if normalized in _INDIA_ALIASES else "overseas"


def is_overseas_player(player: dict | None) -> bool:
    """Classify a player row/dict using its ``country`` field."""
    return is_overseas_country((player or {}).get("country"))


def count_overseas(players: Iterable[dict] | None) -> int:
    """Count explicitly identified overseas players in a player collection."""
    return sum(1 for player in (players or []) if is_overseas_player(player))


def within_overseas_limit(players: Iterable[dict] | None) -> bool:
    """Return True when a lineup contains at most four overseas players."""
    return count_overseas(players) <= MAX_OVERSEAS_PLAYERS
