"""Dedicated Telegram custom-emoji registry for Play ILT.

Put the premium emoji ID of each team inside ILT_TEAM_EMOJIS. A value of None
keeps the ordinary fallback marker.
"""

from __future__ import annotations

                                                                     
ILT_TEAM_EMOJIS: dict[str, str | None] = {
    "ADKR": "6161294808481537396",
    "DV": "6159064711137599084",
    "DC": "6161120570248272152",
    "GG": "6158676158331231427",
    "MIE": "6161451935565092976",
    "SW": "6160984286641002515",
}

ILT_TEAM_FALLBACK_EMOJIS: dict[str, str] = {
    "ADKR": "🟣",
    "DV": "🟠",
    "DC": "🔵",
    "GG": "🟠",
    "MIE": "🔵",
    "SW": "🟡",
}


def get_ilt_team_emoji(team_code: str) -> str | None:
    from services.tournament_virtual import base_code
    return ILT_TEAM_EMOJIS.get(base_code(team_code))


def get_ilt_team_fallback_emoji(team_code: str) -> str:
    from services.tournament_virtual import base_code
    return ILT_TEAM_FALLBACK_EMOJIS.get(base_code(team_code), "🏏")


def ilt_team_emoji_html(team_code: str) -> str:
    code = str(team_code or "").strip().upper()
    custom_id = get_ilt_team_emoji(code)
    fallback = get_ilt_team_fallback_emoji(code)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback
