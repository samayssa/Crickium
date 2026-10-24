from __future__ import annotations

print("upload_prob.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.probability_profiles_repo import (
    parse_probability_upload,
    upsert_probability_profile,
    format_upload_report,
)
from database.access_repo import has_upload_access


def _extract_text(message: dict | None) -> str:
    if not message:
        return ""
    return str(message.get("text") or message.get("caption") or "")


@register("upload_prob")
async def upload_prob_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username")

    print(f"[upload_prob] invoked by user_id={user_id} username=@{username}")

    if user_id != ADMIN_USER_ID and not await has_upload_access(user_id):
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner, or users the owner has granted access to via /access."
        )
        return

    command_text = _extract_text(message)
    reply = message.get("reply_to_message")
    reply_text = _extract_text(reply)

    if not reply_text:
        await app.send_message(
            chat_id,
            "⚠️ Please reply to a probability rows message while sending /upload_prob.\n\n"
            "Example command:\n"
            "`/upload_prob [BAT 60-70,BOWL 70-80][OUTSWING][WST,FL][FRONT,GROUND][STRAIGHT_DRIVE][DUSTY][1-6][BALLS_FACED 5-15]`",
            parse_mode="Markdown",
        )
        return

    selectors, probabilities, errors = await parse_probability_upload(command_text, reply_text)
    if selectors is None or probabilities is None:
        report = "📋 *Probability Upload Report*\n\n❌ No valid probability rows were found."
        if errors:
            report += "\n\n*Problems:*\n" + "\n".join(f"• {err}" for err in errors[:20])
        await app.send_message(chat_id, report, parse_mode="Markdown")
        return

    summary = await upsert_probability_profile(
        selectors,
        probabilities,
        created_by=user_id,
        updated_by=user_id,
    )
    report = format_upload_report(summary, errors)
    await app.send_message(chat_id, report, parse_mode="Markdown")
