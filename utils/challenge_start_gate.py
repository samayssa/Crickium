from __future__ import annotations

import html
import os
import re

from app import app
from database.query import fetchval


def _bot_username() -> str:
    username = str(os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot")).strip().lstrip("@")
    return username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else "CrickiumBot"


def start_bot_keyboard() -> dict:
    username = _bot_username()
    return {
        "inline_keyboard": [[
            {
                "text": f"Start {username} to play",
                "url": f"https://t.me/{username}?start=challenge",
                "style": "primary",
            }
        ]]
    }


def _target_label(user_id: int | None, username: str | None, first_name: str | None) -> str:
    if user_id:
        label = html.escape(str(first_name or username or "You"))
        return f'<a href="tg://user?id={int(user_id)}">{label}</a>'
    if username:
        return f"@{html.escape(str(username).lstrip('@'))}"
    return "This user"


async def has_started_bot(user_id: int) -> bool:
    row = await fetchval(
        "SELECT 1 FROM bot_started_users WHERE user_id=$1 LIMIT 1;",
        int(user_id),
    )
    return row is not None


async def require_started_user(
    chat_id: int,
    user_id: int | None,
    username: str | None = None,
    first_name: str | None = None,
) -> bool:
    if user_id and await has_started_bot(int(user_id)):
        return True

    target = _target_label(user_id, username, first_name)
    bot_name = html.escape(_bot_username())
    await app.send_message(
        int(chat_id),
        f"⚠️ <b>{target}, you haven't started {bot_name} yet.</b>\n\n"
        f"Please open {bot_name} and press Start first to play.",
        parse_mode="HTML",
        reply_markup=start_bot_keyboard(),
    )
    return False
