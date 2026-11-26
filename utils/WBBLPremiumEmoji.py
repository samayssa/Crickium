"""Dedicated Telegram custom-emoji registry for Play WBBL.

Put the premium emoji ID of each team inside WBBL_TEAM_EMOJIS. A value of None
keeps the ordinary fallback marker.
"""

from __future__ import annotations

                                                                     
WBBL_TEAM_EMOJIS: dict[str, str | None] = {
    "ADS": "6106995169045780956",
    "BRH": "6107180471114800777",
    "HBH": "6107146317534861964",
    "MLR": "6107147812183480587",
    "MLS": "6106994438901340580",
    "PRS": "6107367933552367139",
    "SYS": "6107408684202072495",
    "SYT": "6107276983324909089",
}

WBBL_TEAM_FALLBACK_EMOJIS: dict[str, str] = {
    "ADS": "🔵",
    "BRH": "🟠",
    "HBH": "🟣",
    "MLR": "🔴",
    "MLS": "🟢",
    "PRS": "🟠",
    "SYS": "🩷",
    "SYT": "🟡",
}


def get_wbbl_team_emoji(team_code: str) -> str | None:
    from services.tournament_virtual import base_code
    return WBBL_TEAM_EMOJIS.get(base_code(team_code))


def get_wbbl_team_fallback_emoji(team_code: str) -> str:
    from services.tournament_virtual import base_code
    return WBBL_TEAM_FALLBACK_EMOJIS.get(base_code(team_code), "🏏")


def wbbl_team_emoji_html(team_code: str) -> str:
    code = str(team_code or "").strip().upper()
    custom_id = get_wbbl_team_emoji(code)
    fallback = get_wbbl_team_fallback_emoji(code)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback
