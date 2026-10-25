from __future__ import annotations

from services.tournament_virtual import base_code as _vbase

# Official WPL franchise set currently consists of five teams.
# Kept separate from the IPL registry so WPL team validation and uploads
# cannot accidentally accept an IPL franchise code.
TEAM_MAP = {
    "DC": "Delhi Capitals",
    "GG": "Gujarat Giants",
    "MI": "Mumbai Indians",
    "RCB": "Royal Challengers Bengaluru",
    "UPW": "UP Warriorz",
}

TEAM_ORDER = ["DC", "GG", "MI", "RCB", "UPW"]

TEAM_COLOR = {
    "DC": "🔵",
    "GG": "🟠",
    "MI": "🔵",
    "RCB": "🔴",
    "UPW": "🩷",
}


def normalize_team_keyword(value: str):
    raw = str(value or "").strip().upper()
    if raw.startswith("WPL-"):
        raw = raw[4:]
    return raw if raw in TEAM_MAP else None


def team_name(code: str) -> str:
    # Tournament teams use virtual codes (UPW12): show the franchise they are dressed as.
    return TEAM_MAP[_vbase(code)]


def team_short(code: str) -> str:
    return _vbase(code)


def team_color(code: str) -> str:
    return TEAM_COLOR.get(_vbase(code), "🏏")


def team_label(code: str) -> str:
    from utils.WPLPremiumEmoji import wpl_team_emoji_html
    code = _vbase(code)
    return f"{wpl_team_emoji_html(code)} {team_name(code)}"


def team_short_label(code: str) -> str:
    from utils.WPLPremiumEmoji import wpl_team_emoji_html
    code = _vbase(code)
    return f"{wpl_team_emoji_html(code)} {team_short(code)}"


def team_button_label(code: str) -> str:
    code = _vbase(code)
    return f"{team_color(code)} {team_short(code)}"
