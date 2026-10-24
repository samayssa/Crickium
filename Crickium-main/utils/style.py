"""Helpers to convert compact player style codes into readable text."""

from __future__ import annotations

import re

_CLEAN_RE = re.compile(r"[^A-Z0-9]+")

BAT_STYLE_ALIASES = {
    "R": "RH",
    "RH": "RH",
    "RIGHT": "RH",
    "RIGHTHAND": "RH",
    "RIGHTHANDED": "RH",
    "RIGHTHANDBATSMAN": "RH",
    "RHBAT": "RH",
    "RHBATSMAN": "RH",
    "RHBATSMAN1": "RH",
    "L": "LH",
    "LH": "LH",
    "LEFT": "LH",
    "LEFTHAND": "LH",
    "LEFTHANDED": "LH",
    "LEFTHANDBATSMAN": "LH",
    "LHBAT": "LH",
    "LHBATSMAN": "LH",
}

BOWL_STYLE_ALIASES = {
    "RAF": "RAF",
    "RIGHTARMFAST": "RAF",
    "RIGHTARMFASTBOWLER": "RAF",
    "RIGHTARMPACER": "RAF",
    "RAP": "RAF",
    "LAF": "LAF",
    "LEFTARMFAST": "LAF",
    "LEFTARMFASTBOWLER": "LAF",
    "LEFTARMPACER": "LAF",
    "LAP": "LAF",
    "RAM": "RAM",
    "RIGHTARMMEDIUM": "RAM",
    "RIGHTARMMEDIUMPACER": "RAM",
    "LAM": "LAM",
    "LEFTARMMEDIUM": "LAM",
    "LEFTARMMEDIUMPACER": "LAM",
    "RAO": "RAO",
    "RIGHTARMOFFBREAK": "RAO",
    "RIGHTARMOFFSPIN": "RAO",
    "RIGHTARMOFFSPINNER": "RAO",
    "LAO": "LAO",
    "LEFTARMOFFBREAK": "LAO",
    "LEFTARMOFFSPIN": "LAO",
    "LEFTARMOFFSPINNER": "LAO",
    "RAL": "RAL",
    "RIGHTARMLEGSPIN": "RAL",
    "RIGHTARMLEGSPINNER": "RAL",
    "LAL": "LAL",
    "LEFTARMLEGSPIN": "LAL",
    "LEFTARMLEGSPINNER": "LAL",
}

BATTING_STYLE_TEXT = {
    "RH": "Right Hand Bat",
    "LH": "Left Hand Bat",
}

BOWLING_STYLE_TEXT = {
    "RAF": "Right Arm Fast",
    "LAF": "Left Arm Fast",
    "RAM": "Right Arm Medium",
    "LAM": "Left Arm Medium",
    "RAO": "Right Arm Off Break",
    "LAO": "Left Arm Off Break",
    "RAL": "Right Arm Leg Spin",
    "LAL": "Left Arm Leg Spin",
}


def _normalize_token(raw: object | None) -> str:
    return _CLEAN_RE.sub("", str(raw or "")).upper()


def batting_style_code(raw: object | None) -> str | None:
    token = _normalize_token(raw)
    if not token:
        return None
    return BAT_STYLE_ALIASES.get(token)


def bowling_style_code(raw: object | None) -> str | None:
    token = _normalize_token(raw)
    if not token:
        return None
    return BOWL_STYLE_ALIASES.get(token)


def batting_style_text(raw: object | None) -> str:
    code = batting_style_code(raw)
    if code:
        return BATTING_STYLE_TEXT.get(code, code)
    value = str(raw or "").strip()
    return value if value else "Unknown"


def bowling_style_text(raw: object | None) -> str:
    code = bowling_style_code(raw)
    if code:
        return BOWLING_STYLE_TEXT.get(code, code)
    value = str(raw or "").strip()
    return value if value else "Unknown"


def describe_player_styles(player: dict) -> dict[str, str]:
    return {
        "batting": batting_style_text(player.get("batting_hand")),
        "bowling": bowling_style_text(player.get("bowling_hand")),
    }


__all__ = [
    "batting_style_code",
    "bowling_style_code",
    "batting_style_text",
    "bowling_style_text",
    "describe_player_styles",
    "BATTING_STYLE_TEXT",
    "BOWLING_STYLE_TEXT",
]
