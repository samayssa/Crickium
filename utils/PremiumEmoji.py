"""Telegram custom-emoji registry for Crickium.

The values in this module are the custom_emoji_id values supplied for the
IPL franchise logos.  Callers can import IPL_TEAM_EMOJIS or get_ipl_team_emoji.
"""

from __future__ import annotations

IPL_TEAM_EMOJIS: dict[str, str] = {
    "CSK": "6233466459670978723",
    "DC": "6231082473648823467",
    "GT": "6230832996178468360",
    "KKR": "6233528822596115283",
    "LSG": "6231206812952042824",
    "MI": "6230762447045664130",
    "PBKS": "6233201756541558942",
    "RR": "6233339143955421619",
    "RCB": "6230812285846166768",
    "SRH": "6233038457589997162",
}


TEAM_FALLBACK_EMOJIS: dict[str, str] = {
    "CSK": "🟡",
    "DC": "🔵",
    "GT": "🔷",
    "KKR": "🟣",
    "LSG": "🔴",
    "MI": "🔵",
    "PBKS": "🔴",
    "RR": "🩷",
    "RCB": "🔴",
    "SRH": "🟠",
}


def get_ipl_team_emoji(team_code: str) -> str | None:
    """Return the configured custom emoji ID for an IPL team code."""
    return IPL_TEAM_EMOJIS.get(str(team_code or "").strip().upper())


def get_ipl_team_fallback_emoji(team_code: str) -> str:
    return TEAM_FALLBACK_EMOJIS.get(str(team_code or "").strip().upper(), "🏏")


def ipl_team_emoji_html(team_code: str) -> str:
    """Return a Telegram HTML custom-emoji entity with a safe Unicode fallback."""
    code = str(team_code or "").strip().upper()
    custom_id = get_ipl_team_emoji(code)
    fallback = get_ipl_team_fallback_emoji(code)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback
