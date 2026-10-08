"""Dedicated Telegram custom-emoji registry for Play WPL.

The baseline ZIP contains IPL logo custom-emoji IDs but no WPL-specific IDs.
WPL slots intentionally remain empty until real Telegram custom-emoji IDs are
provided. The helpers always fall back to ordinary Unicode team markers.
"""

from __future__ import annotations

# Fill these with real Telegram custom_emoji_id values when supplied.
WPL_TEAM_EMOJIS: dict[str, str | None] = {
    "DC": "6158976462444566786",
    "GG": "6158853428811404166",
    "MI": "6158752316691321279",
    "RCB": "6158851204018345083",
    "UPW": "6158968740093367071",
}

WPL_TEAM_FALLBACK_EMOJIS: dict[str, str] = {
    "DC": "🔵",
    "GG": "🟠",
    "MI": "🔵",
    "RCB": "🔴",
    "UPW": "🩷",
}


def get_wpl_team_emoji(team_code: str) -> str | None:
    return WPL_TEAM_EMOJIS.get(str(team_code or "").strip().upper())


def get_wpl_team_fallback_emoji(team_code: str) -> str:
    return WPL_TEAM_FALLBACK_EMOJIS.get(
        str(team_code or "").strip().upper(), "🏏"
    )


def wpl_team_emoji_html(team_code: str) -> str:
    code = str(team_code or "").strip().upper()
    custom_id = get_wpl_team_emoji(code)
    fallback = get_wpl_team_fallback_emoji(code)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback
