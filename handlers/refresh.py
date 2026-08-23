from __future__ import annotations

from app import app
from config import ADMIN_USER_ID
from database.squads_repo import refresh_all_team_squads
from handlers.registry import register


@register("refresh")
async def refresh_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)

    if user_id != int(ADMIN_USER_ID):
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner only.",
        )
        return

    try:
        updated_users, updated_players = await refresh_all_team_squads()
        await app.send_message(
            chat_id,
            f"✅ <b>Player Data Refresh Complete</b>\n\n"
            f"👥 Squads checked ➤ <b>{updated_users}</b>\n"
            f"🔄 Player snapshots synced ➤ <b>{updated_players}</b>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await app.send_message(
            chat_id,
            "⚠️ <b>Refresh failed.</b> No partial player-data refresh was intentionally applied.",
            parse_mode="HTML",
        )
        print(f"[refresh] Refresh failed: {exc!r}")
