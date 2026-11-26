from __future__ import annotations

from services.tournament_virtual import base_code as _vbase

                                                              
                                                                        
                                                   
TEAM_MAP = {
    "ADKR": "Abu Dhabi Knight Riders",
    "DV": "Desert Vipers",
    "DC": "Dubai Capitals",
    "GG": "Gulf Giants",
    "MIE": "MI Emirates",
    "SW": "Sharjah Warriors",
}

TEAM_ORDER = ["ADKR", "DV", "DC", "GG", "MIE", "SW"]

TEAM_COLOR = {
    "ADKR": "🟣",
    "DV": "🟠",
    "DC": "🔵",
    "GG": "🟠",
    "MIE": "🔵",
    "SW": "🟡",
}


def normalize_team_keyword(value: str):
    raw = str(value or "").strip().upper()
    if raw.startswith("ILT-"):
        raw = raw[4:]
    return raw if raw in TEAM_MAP else None


def team_name(code: str) -> str:
                                                                                         
    return TEAM_MAP[_vbase(code)]


def team_short(code: str) -> str:
    return _vbase(code)


def team_color(code: str) -> str:
    return TEAM_COLOR.get(_vbase(code), "🏏")


def team_label(code: str) -> str:
    from utils.ILTPremiumEmoji import ilt_team_emoji_html
    code = _vbase(code)
    return f"{ilt_team_emoji_html(code)} {team_name(code)}"


def team_short_label(code: str) -> str:
    from utils.ILTPremiumEmoji import ilt_team_emoji_html
    code = _vbase(code)
    return f"{ilt_team_emoji_html(code)} {team_short(code)}"


def team_button_label(code: str) -> str:
    code = _vbase(code)
    return f"{team_color(code)} {team_short(code)}"
