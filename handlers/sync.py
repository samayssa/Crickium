from __future__ import annotations

print("sync.py loaded")

from datetime import datetime, timezone

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.backup_repo import export_tables, BACKUP_FORMAT_VERSION


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
    await app.send_message(
        chat_id,
        "⏳ Building a complete database backup (all public application tables)...",
    )

    try:
        # None is intentional: database.backup_repo resolves the full current
        # table set directly from PostgreSQL, so new tables cannot be silently
        # forgotten when the schema evolves.
        backup_bytes = await export_tables(None, backup_type="sync")
    except Exception as exc:
        print(f"[sync] Backup failed: {exc!r}")
        await app.send_message(
            chat_id,
            f"❌ *Backup failed.*\n`{exc}`",
            parse_mode="Markdown",
        )
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    await app.send_document(
        chat_id,
        backup_bytes,
        filename=f"cricklum_full_backup_v{BACKUP_FORMAT_VERSION}_{timestamp}.json.gz",
        caption=(
            "🗂 *Complete database backup.*\n"
            "Includes every current public application table (including runtime/game recovery tables).\n"
            "Reply to this file with /recover to restore the exact database state represented by this backup."
        ),
        parse_mode="Markdown",
    )
    print(f"[sync] Full backup sent to user_id={user_id}, {len(backup_bytes)} bytes")
