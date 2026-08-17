from __future__ import annotations

print("give.py loaded")

from database.query import execute, fetchrow, transaction
from handlers.registry import register
from app import app
from utils.mentions import mention


async def _ensure_user(user_id: int, username: str | None, first_name: str | None) -> None:
    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_seen_at = NOW();
        """,
        user_id, username, first_name,
    )


def _parse_amount(tokens: list[str]) -> int | None:
    for token in tokens:
        cleaned = token.replace(",", "").strip()
        if cleaned.isdigit():
            amount = int(cleaned)
            if amount > 0:
                return amount
    return None


def _parse_username(tokens: list[str]) -> str | None:
    for token in tokens:
        token = token.strip()
        if token.startswith("@") and len(token) > 1:
            return token[1:]
    return None


async def _transfer_funds(sender_id: int, recipient_id: int, amount: int) -> tuple[int, int]:
    async def _tx(conn):
        sender_balance = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1;", sender_id)
        sender_balance = int(sender_balance or 0)
        if sender_balance < amount:
            return sender_balance, -1

        recipient_exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1;", recipient_id)
        if not recipient_exists:
            return sender_balance, -2

        await conn.execute(
            "UPDATE users SET balance = balance - $1, total_spent = total_spent + $1 WHERE user_id = $2;",
            amount, sender_id,
        )
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE user_id = $2;",
            amount, recipient_id,
        )
        new_sender_balance = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1;", sender_id)
        return int(new_sender_balance or 0), amount

    return await transaction(_tx)


@register("give")
async def give_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    sender_id = from_user.get("id")
    sender_username = from_user.get("username")
    sender_first_name = from_user.get("first_name")
    text = (message.get("text") or "").strip()

    print(f"[give] /give invoked by user_id={sender_id}")

    await _ensure_user(sender_id, sender_username, sender_first_name)

    reply = message.get("reply_to_message") or {}
    recipient_user = reply.get("from") if reply else None
    recipient_id = recipient_user.get("id") if recipient_user else None
    recipient_username = recipient_user.get("username") if recipient_user else None
    recipient_first_name = recipient_user.get("first_name") if recipient_user else None

    tokens = text.split()[1:]
    amount = _parse_amount(tokens)

    if amount is None:
        await app.send_message(
            chat_id,
            "*⚠️ Invalid amount.*\n\nUse `/give @username 1000` or reply to a user with `/give 1000`.",
            parse_mode="Markdown",
        )
        return

    if recipient_id is None:
        recipient_username = _parse_username(tokens)
        if not recipient_username:
            await app.send_message(
                chat_id,
                "*⚠️ Recipient not found.*\n\nUse `/give @username 1000` or reply to the user with `/give 1000`.",
                parse_mode="Markdown",
            )
            return

        row = await fetchrow(
            "SELECT user_id, username, first_name FROM users WHERE LOWER(username) = LOWER($1) LIMIT 1;",
            recipient_username,
        )
        if not row:
            await app.send_message(
                chat_id,
                f"*⚠️ @{recipient_username} is not in my database yet.*\n\nAsk that user to /start the bot first.",
                parse_mode="Markdown",
            )
            return

        recipient_id = int(row["user_id"])
        recipient_username = row.get("username")
        recipient_first_name = row.get("first_name")
    else:
        # Reply-based give can safely ensure the recipient user is in DB.
        await _ensure_user(recipient_id, recipient_username, recipient_first_name)

    if recipient_id == sender_id:
        await app.send_message(chat_id, "*🚫 You cannot send coins to yourself.*", parse_mode="Markdown")
        return

    sender_display = mention(sender_id, sender_username, sender_first_name)
    recipient_display = mention(recipient_id, recipient_username, recipient_first_name)

    try:
        new_sender_balance, status = await _transfer_funds(sender_id, recipient_id, amount)
    except Exception as exc:
        print(f"[give] !! transfer failed: {exc!r}")
        await app.send_message(
            chat_id,
            "*⚠️ Transfer failed due to a database error.*\n\nPlease try again.",
            parse_mode="Markdown",
        )
        return

    if status == -1:
        await app.send_message(
            chat_id,
            "*🚫 Insufficient balance.*\n\nYou do not have enough coins to send this amount.",
            parse_mode="Markdown",
        )
        return

    if status == -2:
        await app.send_message(
            chat_id,
            f"*⚠️ {recipient_display} does not have an account yet.*\n\nAsk them to /start first.",
            parse_mode="Markdown",
        )
        return

    text_out = (
        "*✅ TRANSFER COMPLETE*\n"
        "──────────────────\n"
        f"{sender_display}  ➜  {recipient_display}\n\n"
        f"  🪙 {amount:,}\n\n"
        "──────────────────\n"
        f"*💼 Balance:* 🪙 {new_sender_balance:,}"
    )
    await app.send_message(chat_id, text_out, parse_mode="Markdown")
    print(f"[give] transfer ok: sender={sender_id} recipient={recipient_id} amount={amount}")