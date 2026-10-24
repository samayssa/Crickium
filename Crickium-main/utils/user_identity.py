"""User-owned franchise identity helpers: team name + optional logo."""
from __future__ import annotations

import html
import re
from typing import Any

from database.query import fetchrow


def custom_emoji_html(custom_emoji_id: str | int | None, fallback: str = "🏷️") -> str:
    value = str(custom_emoji_id or "").strip()
    if value.isdigit():
        return f'<tg-emoji emoji-id="{html.escape(value, quote=True)}">{fallback}</tg-emoji>'
    return fallback


def escape_team_name(team_name: str | None) -> str:
    return html.escape(str(team_name or "").strip())


async def get_user_identity(user_id: int, default_name: str | None = None) -> dict[str, Any]:
    row = await fetchrow(
        """
        SELECT franchise_name, team_logo_type, team_logo_id, team_logo_fallback
        FROM users WHERE user_id = $1;
        """,
        int(user_id),
    )
    name = str(row["franchise_name"] or "").strip() if row else ""
    if not name:
        name = f"{(default_name or 'Player').strip()} XI"

    logo_type = str(row["team_logo_type"] or "").strip().lower() if row else ""
    logo_id = str(row["team_logo_id"] or "").strip() if row else ""
    logo_fallback = str(row["team_logo_fallback"] or "🏷️").strip() if row else "🏷️"

    if logo_type == "custom_emoji" and logo_id:
        logo = custom_emoji_html(logo_id, logo_fallback)
    elif logo_type == "sticker" and logo_id:
        # Telegram stickers cannot be embedded inline inside ordinary text.
        # Persist/use the sticker's own emoji as the inline visual fallback.
        logo = logo_fallback or "🏷️"
    else:
        logo = ""

    return {
        "team_name": name,
        "logo_type": logo_type,
        "logo_id": logo_id,
        "logo_fallback": logo_fallback,
        "logo": logo,
        "identity": f"{logo} {escape_team_name(name)}".strip() if logo else escape_team_name(name),
    }


def inline_identity(identity: dict[str, Any]) -> str:
    return str(identity.get("identity") or escape_team_name(identity.get("team_name")))


def looks_like_custom_emoji_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d{8,25}", str(value or "").strip()))
