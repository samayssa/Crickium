from __future__ import annotations

print("tip.py loaded")

import html

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.query import execute, fetchrow, transaction
from utils.mentions import mention


async def _ensure_user(user_id: int, username: str | None, first_name: str | None) -> None:
    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET username = EXCLUDED.username,
                      first_name = EXCLUDED.first_name,
                      last_seen_at = NOW();
        """,
        int(user_id), username, first_name,
    )


def _parse_tokens(text: str) -> list[str]:
    return (text or "").split()[1:]


def _parse_amount(tokens: list[str]) -> int | None:
    for token in reversed(tokens):
        cleaned = token.replace(",", "").strip()
        if cleaned.isdigit() and int(cleaned) > 0:
            return int(cleaned)
    return None


def _parse_currency(tokens: list[str]) -> str:
    lowered = {t.lower() for t in tokens}
    if lowered.intersection({"rubi", "ruby", "rubies"}):
        return "rubies"
    return "coins"


def _parse_username(tokens: list[str]) -> str | None:
    for token in tokens:
        token = token.strip()
        if token.startswith("@") and len(token) > 1:
            return token[1:]
    return None


def _parse_user_id(tokens: list[str]) -> int | None:
    for token in tokens:
        cleaned = token.strip()
        if cleaned.isdigit():
            value = int(cleaned)
            if value > 0:
                return value
    return None


@register("tip")
async def tip_command(message):
    chat_id = int(message["chat"]["id"])
    from_user = message.get("from") or {}
    sender_id = int(from_user.get("id") or 0)

    # /tip is an owner/Edwin-only administrative command.
    if sender_id != int(ADMIN_USER_ID):
        await app.send_message(
            chat_id,
            "<b>🚫 This command is restricted to Edwin only.</b>",
            parse_mode="HTML",
        )
        return

    text = (message.get("text") or "").strip()
    tokens = _parse_tokens(text)
    amount = _parse_amount(tokens)
    if amount is None:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please provide a valid amount.</b>\n"
            "Use <code>/tip @username 50000</code> or reply to a user with <code>/tip 50000</code>.",
            parse_mode="HTML",
        )
        return

    currency = _parse_currency(tokens)

    # Reply target has priority.
    reply = message.get("reply_to_message") or {}
    reply_user = reply.get("from") or {}
    target_id = int(reply_user.get("id") or 0) or None
    target_username = reply_user.get("username")
    target_first_name = reply_user.get("first_name")

    if target_id is None:
        target_id = _parse_user_id(tokens)

    if target_id is None:
        target_username = _parse_username(tokens)
        if target_username:
            target = await fetchrow(
                "SELECT user_id, username, first_name FROM users WHERE LOWER(username) = LOWER($1) LIMIT 1;",
                target_username,
            )
            if target:
                target_id = int(target["user_id"])
                target_username = target["username"]
                target_first_name = target["first_name"]

    if target_id is None:
        await app.send_message(
            chat_id,
            "<b>⚠️ Recipient not found.</b>\n"
            "Use <code>/tip @username 50000</code>, <code>/tip 123456789 50000</code>, "
            "or reply to a user's message with <code>/tip 50000</code>.",
            parse_mode="HTML",
        )
        return

    target_row = await fetchrow(
        "SELECT user_id, username, first_name FROM users WHERE user_id = $1;",
        target_id,
    )
    if not target_row:
        await app.send_message(
            chat_id,
            "<b>⚠️ This user is not in my database yet.</b>\nAsk them to <code>/start</code> the bot first.",
            parse_mode="HTML",
        )
        return

    target_username = target_row["username"] or target_username
    target_first_name = target_row["first_name"] or target_first_name

    async def _tx(conn):
        column = "rubies" if currency == "rubies" else "balance"
        await conn.execute(
            f"UPDATE users SET {column} = {column} + $1 WHERE user_id = $2;",
            amount,
            target_id,
        )
        return await conn.fetchrow(
            "SELECT username, first_name FROM users WHERE user_id = $1;",
            target_id,
        )

    await transaction(_tx)

    display = mention(target_id, target_username, target_first_name)
    asset_line = (
        f"💎 +{amount:,} Rubies" if currency == "rubies"
        else f"🪙 +{amount:,} Coins"
    )
    text_out = (
        "╭━━━〔 💰 COINS TIPPED 〕━━━╮\n\n"
        f"👤 {display}\n"
        f"{asset_line}\n\n"
        "✅ Successfully credited!\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯"
    )
    await app.send_message(chat_id, text_out, parse_mode="HTML")
