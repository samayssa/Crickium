from __future__ import annotations
from datetime import datetime


def format_group_notification(*, group_name: str, group_link: str, username: str | None, user_id: int, group_id: int, time_text: str | None = None) -> str:
    username_text = f"@{username}" if username else "N/A"
    added = time_text or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🟢 Crickium Added to Group\n\n"
        f"👥 Group: {group_name}\n"
        f"🔗 Group Link: {group_link}\n"
        f"👤 Added By: {username_text}\n"
        f"🆔 User ID: {user_id}\n\n"
        f"📌 Group ID: {group_id}\n"
        f"⏱️ Added At: {added}"
    )
