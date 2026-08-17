from __future__ import annotations

print("recover.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.backup_repo import import_tables


async def _is_admin(user_id: int | None) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


@register("recover")
async def recover_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")

    if not await _is_admin(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return

    reply_to = message.get("reply_to_message")
    document = (reply_to or {}).get("document")
    if not reply_to or not document or not document.get("file_id"):
        await app.send_message(
            chat_id,
            "⚠️ Please use /recover as a reply to a backup document "
            "sent by /cleardata or /sync.",
        )
        return

    print(f"[recover] /recover invoked by user_id={user_id}, file_id={document['file_id'][:20]}...")
    await app.send_message(chat_id, "⏳ Restoring from backup...")

    try:
        raw_bytes = await app.download_media(document["file_id"])
        results = await import_tables(raw_bytes)
    except Exception as exc:
        print(f"[recover] Restore failed: {exc!r}")
        await app.send_message(
            chat_id,
            f"❌ *Restore failed.*\n`{exc}`\n\nMake sure this is a genuine backup file from /cleardata or /sync.",
            parse_mode="Markdown",
        )
        return

    lines = [f"• `{table}` — {count} row(s)" for table, count in results.items()]
    summary = "\n".join(lines) if lines else "_(no tables in this backup)_"
    await app.send_message(
        chat_id,
        f"✅ *Restore completed.*\n\n{summary}",
        parse_mode="Markdown",
    )
    print(f"[recover] Restore completed by user_id={user_id}: {results}")
