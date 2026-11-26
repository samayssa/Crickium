from __future__ import annotations

from typing import Any, Iterable

from utils.overseas import normalize_country

MAX_OVERSEAS_PLAYERS = 4

_AUSTRALIA_ALIASES = {
    "australia",
    "australian",
    "aus",
    "au",
    "commonwealth of australia",
    "🇦🇺",
}


def is_australian_country(country: Any) -> bool:
    return normalize_country(country) in _AUSTRALIA_ALIASES


def is_overseas_country(country: Any) -> bool:
    normalized = normalize_country(country)
    return bool(normalized) and normalized not in _AUSTRALIA_ALIASES


def is_overseas_player(player: dict | None) -> bool:
    return is_overseas_country((player or {}).get("country"))


def count_overseas(players: Iterable[dict] | None) -> int:
    return sum(1 for player in (players or []) if is_overseas_player(player))


def within_overseas_limit(players: Iterable[dict] | None) -> bool:
    return count_overseas(players) <= MAX_OVERSEAS_PLAYERS
