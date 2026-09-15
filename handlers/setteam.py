from __future__ import annotations

import html

from app import app
from database.team_identity_repo import set_team_name
from database.user_stats_repo import ensure_franchise_name
from handlers.registry import register

MAX_TEAM_NAME_LENGTH = 64


@register("setteam")
async def setteam_command(message):
    chat_id = int(message["chat"]["id"])
    from_user = message.get("from", {})
    user_id = int(from_user.get("id") or 0)
    text = str(message.get("text") or "")
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await app.send_message(
            chat_id,
            "⚠️ Use <code>/setteam &lt;your team name&gt;</code>.\n\n"
            "Example: <code>/setteam Samay Super Kings</code>",
            parse_mode="HTML",
        )
        return

    team_name = " ".join(parts[1].strip().split())[:MAX_TEAM_NAME_LENGTH].strip()
    if not team_name:
        await app.send_message(chat_id, "⚠️ Team name cannot be empty.")
        return

    await set_team_name(user_id, team_name, from_user.get("first_name"))
    await ensure_franchise_name(user_id, from_user.get("first_name"))
    await app.send_message(
        chat_id,
        "✅ <b>Team name updated.</b>\n\n"
        f"🎽 <b>Team ➤</b> {html.escape(team_name)}",
        parse_mode="HTML",
    )
