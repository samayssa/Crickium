from __future__ import annotations

from typing import Any, Iterable

from utils.overseas import normalize_country

MIN_UAE = 2
MIN_AFGHANISTAN = 2

_UAE = {"uae", "united arab emirates", "u.a.e", "u.a.e.", "emirati", "🇦🇪"}
_AFGHANISTAN = {"afghanistan", "afghan", "afg", "🇦🇫"}


def country_group(country: Any) -> str:
    value = normalize_country(country)
    if value in _UAE:
        return "uae"
    if value in _AFGHANISTAN:
        return "afghanistan"
    return "other"


def country_counts(players: Iterable[dict] | None) -> dict[str, int]:
    counts = {"uae": 0, "afghanistan": 0, "other": 0}
    for player in players or []:
        counts[country_group((player or {}).get("country"))] += 1
    return counts


def country_balance_ok(players: Iterable[dict] | None) -> bool:
    counts = country_counts(players)
    return counts["uae"] >= MIN_UAE and counts["afghanistan"] >= MIN_AFGHANISTAN
