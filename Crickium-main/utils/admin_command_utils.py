"""Shared helpers for owner-only commands that must report privately."""
from __future__ import annotations

from typing import Any

from app import app
from config import ADMIN_USER_ID
from database.query import fetchrow


def is_bot_owner(message: dict[str, Any]) -> bool:
    try:
        sender_id = int((message.get("from") or {}).get("id") or 0)
        return sender_id != 0 and sender_id == int(ADMIN_USER_ID)
    except (TypeError, ValueError):
        return False


async def resolve_user_reference(reference: str, *, allow_unknown_id: bool = False) -> dict[str, Any] | None:
    """Resolve @username or numeric Telegram ID against the bot's user table.

    Numeric IDs may optionally be returned even when the user has not yet been
    registered in `users`; this is useful for access grants, whose permission
    table intentionally has no foreign key to `users`.
    """
    value = str(reference or "").strip()
    if value.startswith("@"):
        username = value[1:].strip()
        if not username:
            return None
        row = await fetchrow(
            """SELECT user_id, username, first_name
               FROM users
               WHERE LOWER(REPLACE(COALESCE(username, ''), '@', '')) = LOWER($1)
               ORDER BY last_seen_at DESC NULLS LAST
               LIMIT 1;""",
            username,
        )
        return dict(row) if row else None

    if value.isdigit():
        user_id = int(value)
        row = await fetchrow(
            """SELECT user_id, username, first_name
               FROM users WHERE user_id = $1;""",
            user_id,
        )
        if row:
            return dict(row)
        if allow_unknown_id:
            return {"user_id": user_id, "username": None, "first_name": None}
    return None


def user_label(user: dict[str, Any]) -> str:
    user_id = int(user.get("user_id") or 0)
    username = str(user.get("username") or "").strip().lstrip("@")
    first_name = str(user.get("first_name") or "").strip()
    display = first_name or (f"@{username}" if username else "Unknown user")
    if username and first_name:
        display += f" (@{username})"
    return f"{display} [ID: {user_id}]"


async def owner_private_response(message: dict[str, Any], texts: str | list[str]) -> bool:
    """Send command results only to the configured owner, then hide group usage.

    Telegram requires the owner to have opened the bot's private chat before
    the bot can DM them. In groups, deleting the invocation also requires the
    bot to have permission to delete messages. The result is never posted in
    the source group and no target user is notified.
    """
    chunks = [texts] if isinstance(texts, str) else list(texts)
    delivered = True
    for chunk in chunks:
        try:
            await app.send_message(int(ADMIN_USER_ID), str(chunk))
        except Exception as exc:
            delivered = False
            print(f"[admin-command] Could not DM owner {ADMIN_USER_ID}: {exc!r}")
            break

    chat = message.get("chat") or {}
    chat_type = str(chat.get("type") or "").lower()
    if chat_type in {"group", "supergroup"}:
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if chat_id is not None and message_id is not None:
            try:
                await app.delete_message(int(chat_id), int(message_id))
            except Exception as exc:
                print(
                    "[admin-command] Could not delete owner command from group "
                    f"chat_id={chat_id} message_id={message_id}: {exc!r}"
                )
    return delivered
