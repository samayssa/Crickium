"""Telegram custom-emoji registry for Crickium.

The values in this module are the custom_emoji_id values supplied for the
IPL franchise logos.  Callers can import IPL_TEAM_EMOJIS or get_ipl_team_emoji.
"""

from __future__ import annotations

OVR_EMOJI_ID = "5370784581341422520"


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


def ovr_emoji_html() -> str:
    """Return the custom emoji used for OVR labels, with a safe fallback."""
    return f'<tg-emoji emoji-id="{OVR_EMOJI_ID}">⭐</tg-emoji>'

# Milestone notification custom-emoji slots. Keep these as ``None`` until
# Telegram custom-emoji IDs are supplied. The milestone renderer always falls
# back to the Unicode value while they are None, and automatically gives the
# custom emoji first priority once a valid ID is added here.
MILESTONE_LINE_EMOJIS: dict[str, str | None] = {
    "title": None,
    "player": None,
    "game": None,
    "achievement": None,
    "owner": None,
    "stat": None,
}

# Optional media for milestone alerts. Replace ``file_id`` with a Telegram
# animation/GIF or video file_id later. ``None`` means send the normal text
# alert only for now.
MILESTONE_MEDIA: dict[str, dict[str, str | None]] = {
    "BAT_50": {"type": "animation", "file_id": None},
    "BAT_100": {"type": "animation", "file_id": None},
    "BOWL_3": {"type": "animation", "file_id": None},
    "BOWL_5": {"type": "animation", "file_id": None},
    "HAT_TRICK": {"type": "animation", "file_id": None},
    "PARTNERSHIP": {"type": "animation", "file_id": None},
}

MILESTONE_FALLBACK_EMOJIS: dict[str, str] = {
    "BAT_50": "🏏",
    "BAT_100": "💯",
    "BOWL_3": "🎯",
    "BOWL_5": "🔥",
    "HAT_TRICK": "🎩",
    "PARTNERSHIP": "🤝",
}


def milestone_emoji_html(category: str, fallback: str) -> str:
    """Return custom emoji HTML when configured, otherwise Unicode fallback."""
    custom_id = MILESTONE_LINE_EMOJIS.get(str(category))
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback
