"""Per-tournament ("virtual") franchise team codes.

An auction tournament gives every owner a brand new squad, so the match
engines cannot read it from the franchise's normal ``playint_players`` rows.
Instead each tournament team gets its own team code: the franchise code the
owner picked plus the tournament id, e.g. ``CSK`` in tournament 12 -> ``CSK12``.

The suffix is the whole trick: real franchise / nation codes never contain a
digit, so a virtual code can always be turned back into the franchise it is
dressed as (name, colour, emoji) without a database lookup. That keeps the
team-name helpers synchronous and lets the bot process and the Mini App
process resolve virtual teams identically.

This module is pure (no imports from the bot or the database) so the low level
team repositories can import it safely.
"""
from __future__ import annotations

import re

_VIRTUAL_RE = re.compile(r"^([A-Z]{2,6})(\d{1,9})$")


def make_code(base: str, tournament_id: int) -> str:
    base = re.sub(r"[^A-Z]", "", str(base or "").upper())
    if not base:
        raise ValueError("a franchise code is required")
    return f"{base}{int(tournament_id)}"


def is_virtual(code: object) -> bool:
    return bool(_VIRTUAL_RE.match(str(code or "").strip().upper()))


def base_code(code: object) -> str:
    """``CSK12`` -> ``CSK``; real codes (and anything unrecognised) come back upper-cased."""
    raw = str(code or "").strip().upper()
    match = _VIRTUAL_RE.match(raw)
    return match.group(1) if match else raw


def tournament_of(code: object) -> int | None:
    match = _VIRTUAL_RE.match(str(code or "").strip().upper())
    return int(match.group(2)) if match else None
