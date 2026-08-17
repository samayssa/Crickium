from __future__ import annotations

print("sync.py loaded")

from datetime import datetime, timezone

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.backup_repo import export_tables, FULL_BACKUP_TABLES


async def _is_admin(user_id: int | None) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


@register("sync")
async def sync_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")

    if not await _is_admin(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return

    print(f"[sync] /sync invoked by user_id={user_id}")
    await app.send_message(chat_id, "⏳ Building a full backup...")

    try:
        backup_bytes = await export_tables(FULL_BACKUP_TABLES, backup_type="sync")
    except Exception as exc:
        print(f"[sync] Backup failed: {exc!r}")
        await app.send_message(chat_id, f"❌ *Backup failed.*\n`{exc}`", parse_mode="Markdown")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    await app.send_document(
        chat_id,
        backup_bytes,
        filename=f"cricklum_full_backup_{timestamp}.json.gz",
        caption=(
            "🗂 *Full database backup.*\n"
            "Reply to this file with /recover any time to restore the bot to this exact state."
        ),
        parse_mode="Markdown",
    )
    print(f"[sync] Backup sent to user_id={user_id}, {len(backup_bytes)} bytes")
