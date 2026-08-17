print("upload_players.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.players_repo import bulk_upload_players
from database.access_repo import has_upload_access


@register("upload_pl")
async def upload_players_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[upload_pl] Command invoked by user_id={user_id} username=@{from_user.get('username')}")

    # ---- Owner, or a user granted access via /access ----
    if user_id != ADMIN_USER_ID and not await has_upload_access(user_id):
        print(f"[upload_pl] REJECTED: user_id={user_id} is not the bot owner and has no granted access.")
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner, or users the owner has granted access to via /access."
        )
        return

    # ---- Must be used as a reply to a text message ----
    reply_to = message.get("reply_to_message")
    if not reply_to or "text" not in reply_to:
        print("[upload_pl] REJECTED: not used as a reply to a text message.")
        await app.send_message(
            chat_id,
            "⚠️ Please use /upload_pl as a *reply* to a message containing player data.\n\n"
            "Format (one player per line):\n"
            "`[Player Name][Country][Role][RH/LH-BAT <LEVEL>][RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL <LEVEL>]`\n\n"
            "Batting field stays RH/LH-BAT, bowling field now uses arm-style codes like RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL.\n\n"
            "Example:\n"
            "`[Virat Kohli][India][Batsman][RH-BAT 96][RAF 38]`"
        )
        return

    raw_text = reply_to["text"]
    print(f"[upload_pl] Processing replied text ({len(raw_text)} chars)...")

    summary = await bulk_upload_players(raw_text, uploaded_by=user_id)

    lines = [
        "📋 *Player Upload Report*",
        "",
        f"📥 Total lines processed: {summary['total_lines']}",
        f"✅ Newly uploaded: {summary['uploaded']}",
        f"♻️ Already in database: {summary['already_exists']}",
        f"❌ Failed: {summary['failed']}",
    ]

    if summary["failed_details"]:
        lines.append("")
        lines.append("*Failure details:*")
        for detail in summary["failed_details"][:15]:  # cap to avoid huge messages
            lines.append(f"• {detail}")
        if len(summary["failed_details"]) > 15:
            lines.append(f"...and {len(summary['failed_details']) - 15} more.")

    report = "\n".join(lines)
    print(f"[upload_pl] Sending report to chat_id={chat_id}")
    await app.send_message(chat_id, report, parse_mode="Markdown")
    print("[upload_pl] Done.")
