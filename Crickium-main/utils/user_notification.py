from __future__ import annotations
from datetime import datetime


def format_user_notification(*, full_name: str, username: str | None, user_id: int, time_text: str | None = None) -> str:
    username_text = f"@{username}" if username else "N/A"
    joined = time_text or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🟢 New User Joined Crickium\n\n"
        f"👤 Name: {full_name}\n"
        f"🔗 Username: {username_text}\n"
        f"🆔 User ID: {user_id}\n\n"
        "🏏 Status: Started Crickium\n"
        f"⏱️ Joined: {joined}"
    )
