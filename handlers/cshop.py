from __future__ import annotations

print("cshop.py loaded")

import time
import uuid

from app import app
from database.query import execute, fetchrow, transaction
from handlers.registry import register, register_callback
from buttons.cshop_buttons import exchange_confirm_keyboard
from utils.coin_exchange import COIN_EXCHANGE_CHART, coins_for_rubies, format_exchange_chart

REQUEST_TTL_SECONDS = 300
NO_KEYBOARD = {"inline_keyboard": []}


def _bold_blockquote(content: str, *, expandable: bool = False) -> str:
    attr = " expandable" if expandable else ""
    return f"<blockquote{attr}>\n{content}\n</blockquote>"


def _chart_message() -> str:
    title = "<b>╭━━━〔 💎 C SHOP 〕━━━╮</b>"
    body = _bold_blockquote(format_exchange_chart(), expandable=True)
    footer = "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    return f"{title}\n\n{body}\n\n{footer}"


def _parse_rubies(text: str) -> int | None:
    parts = (text or "").split()
    if len(parts) != 2:
        return None
    raw = parts[1].replace(",", "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _confirm_message(rubies: int, coins: int) -> str:
    title = "<b>╭━━〔 💎 CONFIRM EXCHANGE 〕━━╮</b>"
    body = (
        f"<b>💎 {rubies:,} Rubies</b>\n"
        f"<b>　　　↓</b>\n"
        f"<b>🪙 {coins:,} Coins</b>\n\n"
        f"<b>⚠️ {rubies:,} Rubies will be spent.</b>\n\n"
        f"<b>Confirm this exchange?</b>"
    )
    footer = "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    return f"{title}\n\n{_bold_blockquote(body)}\n\n{footer}"


def _status_message(title: str, lines: list[str]) -> str:
    content = "\n".join(f"<b>{line}</b>" for line in lines)
    return f"<b>{title}</b>\n\n{_bold_blockquote(content)}\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"


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
        user_id, username, first_name,
    )


async def _edit_prompt(callback_query: dict, text: str, reply_markup=None) -> None:
    message = callback_query.get("message") or {}
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    await app.edit_message_text(
        chat_id,
        message_id,
        text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


def _expired(created_at) -> bool:
    try:
        age = time.time() - created_at.timestamp()
        return age > REQUEST_TTL_SECONDS
    except Exception:
        return False


@register("cshop")
async def cshop_command(message):
    await app.send_message(message["chat"]["id"], _chart_message(), parse_mode="HTML")


@register("cbuy")
async def cbuy_command(message):
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = int(user.get("id") or 0)

    rubies = _parse_rubies(message.get("text") or "")
    coins = coins_for_rubies(rubies) if rubies is not None else None

    if coins is None:
        await app.send_message(
            chat_id,
            _status_message(
                "╭━━〔 ⚠️ INVALID PACKAGE 〕━━╮",
                [
                    f"💎 {rubies:,} is not an available package." if rubies is not None else "Use /cbuy <exact rubies>.",
                    "",
                    "Available: 100 • 250 • 500 • 750 • 1K",
                    "1.5K • 2K • 3K • 4K • 5K",
                    "",
                    "Use /cshop to view the chart.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    await _ensure_user(user_id, user.get("username"), user.get("first_name"))
    row = await fetchrow("SELECT rubies FROM users WHERE user_id = $1;", user_id)
    balance = int(row["rubies"] or 0) if row else 0

    if balance < rubies:
        await app.send_message(
            chat_id,
            _status_message(
                "╭━━〔 ⚠️ INSUFFICIENT RUBIES 〕━━╮",
                [
                    f"Required: 💎 {rubies:,}",
                    f"Available: 💎 {balance:,}",
                    f"You need 💎 {rubies - balance:,} more.",
                ],
            ),
            parse_mode="HTML",
        )
        return

    request_id = uuid.uuid4()
    await execute(
        """
        INSERT INTO coin_exchange_requests (request_id, user_id, rubies, coins, status)
        VALUES ($1, $2, $3, $4, 'pending');
        """,
        request_id, user_id, rubies, coins,
    )

    await app.send_message(
        chat_id,
        _confirm_message(rubies, coins),
        parse_mode="HTML",
        reply_markup=exchange_confirm_keyboard(str(request_id)),
    )


async def _load_request(conn, request_id: uuid.UUID):
    return await conn.fetchrow(
        """
        SELECT request_id, user_id, rubies, coins, status, created_at
        FROM coin_exchange_requests
        WHERE request_id = $1
        FOR UPDATE;
        """,
        request_id,
    )


def _parse_request_id(data: str) -> uuid.UUID | None:
    try:
        parts = data.split(":", 1)
        if len(parts) != 2:
            return None
        return uuid.UUID(parts[1])
    except (ValueError, TypeError):
        return None


async def _processed_alert(callback_query: dict) -> None:
    await app.answer_callback_query(
        callback_query["id"],
        "⚠️ This exchange has already been processed.",
        show_alert=True,
    )


@register_callback("cshop_confirm")
async def cshop_confirm_callback(callback_query):
    request_id = _parse_request_id(callback_query.get("data") or "")
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if request_id is None:
        await app.answer_callback_query(callback_query["id"], "Invalid exchange request.", show_alert=True)
        return

    async def _tx(conn):
        request = await _load_request(conn, request_id)
        if not request:
            return "missing", None
        if int(request["user_id"]) != user_id:
            return "owner", None
        if request["status"] != "pending":
            return "processed", request
        if _expired(request["created_at"]):
            await conn.execute(
                "UPDATE coin_exchange_requests SET status='expired', processed_at=NOW() WHERE request_id=$1;",
                request_id,
            )
            return "expired", request

        updated = await conn.fetchrow(
            """
            UPDATE users
            SET rubies = rubies - $1,
                balance = balance + $2
            WHERE user_id = $3
              AND COALESCE(rubies, 0) >= $1
            RETURNING rubies, balance;
            """,
            int(request["rubies"]), int(request["coins"]), user_id,
        )
        if not updated:
            await conn.execute(
                "UPDATE coin_exchange_requests SET status='failed', processed_at=NOW() WHERE request_id=$1;",
                request_id,
            )
            return "insufficient", request

        await conn.execute(
            "UPDATE coin_exchange_requests SET status='completed', processed_at=NOW() WHERE request_id=$1;",
            request_id,
        )
        return "completed", (request, updated)

    status, payload = await transaction(_tx)

    if status == "missing":
        await app.answer_callback_query(callback_query["id"], "Exchange request not found.", show_alert=True)
        return
    if status == "owner":
        await app.answer_callback_query(callback_query["id"], "This exchange isn't yours.", show_alert=True)
        return
    if status == "processed":
        await _processed_alert(callback_query)
        return
    if status == "expired":
        await app.answer_callback_query(callback_query["id"], "⏳ This exchange request has expired.", show_alert=True)
        await _edit_prompt(
            callback_query,
            _status_message(
                "╭━━〔 ⏳ REQUEST EXPIRED 〕━━╮",
                ["This exchange request has expired.", "No Rubies were spent.", "Use /cbuy to try again."],
            ),
            NO_KEYBOARD,
        )
        return
    if status == "insufficient":
        await app.answer_callback_query(callback_query["id"], "⚠️ Not enough Rubies.", show_alert=True)
        await _edit_prompt(
            callback_query,
            _status_message(
                "╭━━〔 ⚠️ INSUFFICIENT RUBIES 〕━━╮",
                [
                    f"Required: 💎 {int(payload['rubies']):,}",
                    "Your Ruby balance changed before confirmation.",
                    "No Coins were added.",
                ],
            ),
            NO_KEYBOARD,
        )
        return

    request, updated = payload
    rubies = int(request["rubies"])
    coins = int(request["coins"])
    await app.answer_callback_query(callback_query["id"], "✅ Exchange complete!")
    await _edit_prompt(
        callback_query,
        _status_message(
            "╭━━〔 ✅ EXCHANGE COMPLETE 〕━━╮",
            [f"💎 -{rubies:,} Rubies", f"🪙 +{coins:,} Coins", "Coins added successfully."],
        ),
        NO_KEYBOARD,
    )


@register_callback("cshop_cancel")
async def cshop_cancel_callback(callback_query):
    request_id = _parse_request_id(callback_query.get("data") or "")
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if request_id is None:
        await app.answer_callback_query(callback_query["id"], "Invalid exchange request.", show_alert=True)
        return

    async def _tx(conn):
        request = await _load_request(conn, request_id)
        if not request:
            return "missing", None
        if int(request["user_id"]) != user_id:
            return "owner", None
        if request["status"] != "pending":
            return "processed", request
        if _expired(request["created_at"]):
            await conn.execute(
                "UPDATE coin_exchange_requests SET status='expired', processed_at=NOW() WHERE request_id=$1;",
                request_id,
            )
            return "expired", request
        await conn.execute(
            "UPDATE coin_exchange_requests SET status='cancelled', processed_at=NOW() WHERE request_id=$1;",
            request_id,
        )
        return "cancelled", request

    status, payload = await transaction(_tx)

    if status == "missing":
        await app.answer_callback_query(callback_query["id"], "Exchange request not found.", show_alert=True)
        return
    if status == "owner":
        await app.answer_callback_query(callback_query["id"], "This exchange isn't yours.", show_alert=True)
        return
    if status == "processed":
        await _processed_alert(callback_query)
        return
    if status == "expired":
        await app.answer_callback_query(callback_query["id"], "⏳ This exchange request has expired.", show_alert=True)
        await _edit_prompt(
            callback_query,
            _status_message(
                "╭━━〔 ⏳ REQUEST EXPIRED 〕━━╮",
                ["This exchange request has expired.", "No Rubies were spent.", "Use /cbuy to try again."],
            ),
            NO_KEYBOARD,
        )
        return

    await app.answer_callback_query(callback_query["id"], "❌ Cancelled.")
    await _edit_prompt(
        callback_query,
        _status_message(
            "╭━━〔 ❌ CANCELLED 〕━━╮",
            ["No exchange was made.", "Your Rubies remain unchanged."],
        ),
        NO_KEYBOARD,
    )
