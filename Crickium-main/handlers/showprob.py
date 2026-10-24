from __future__ import annotations

print("showprob.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.probability_profiles_repo import get_probability_system_summary


@register("showprob")
async def showprob_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    if int(user_id or 0) != int(ADMIN_USER_ID):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return

    summary = await get_probability_system_summary()
    text = (
        "📊 *Probability System Report*\n\n"
        f"• Total profiles: {summary['total_profiles']}\n"
        f"• Complete profiles: {summary['complete_profiles']}\n"
        f"• Stored probability values: {summary['total_values']}\n"
        f"• Duplicate values skipped: {summary['duplicate_values']}\n"
        f"• Average profile fill: {summary['average_fill_percent']}%\n"
        f"• Duplicate ratio: {summary['duplicate_ratio_percent']}%\n"
        f"• Probability efficiency: {summary['efficiency_percent']}%\n"
    )
    await app.send_message(chat_id, text, parse_mode="Markdown")
