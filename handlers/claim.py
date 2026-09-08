from __future__ import annotations

print("claim.py loaded")

import asyncio
import html

from pyrogram.errors import ChatWriteForbidden

from handlers.registry import register, register_callback
from app import app
from database.query import execute, fetchrow, transaction
from database.claims_repo import (
    seconds_since_last_claim, get_claim, set_claim_status,
)
from database.squads_repo import get_team_squad, save_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.price_chart import get_price, format_price
from buttons.claim_buttons import retain_release_keyboard
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating
from database.player_user_stats_repo import reset_player_user_stats
from utils.debut_gate import has_completed_debut
from utils.randomiser import get_random_claim_player

CLAIM_COOLDOWN_SECONDS = 3600
CLAIM_ATTEMPT_COOLDOWN_SECONDS = 10
CLAIM_PENDING_TIMEOUT_SECONDS = 60
MAX_SQUAD_SIZE = 25
NO_KEYBOARD = {"inline_keyboard": []}
_CLAIM_AUTO_RELEASE_TASK = None


async def _safe_claim_send_message(chat_id, text, **kwargs):
    """Avoid a handler traceback when Telegram has revoked write rights in a chat."""
    try:
        return await app.send_message(chat_id, text, **kwargs)
    except ChatWriteForbidden as exc:
        print(f"[claim] Telegram denied message write in chat_id={chat_id}: {exc!r}")
        return None


def _format_remaining(seconds: float) -> str:
    remaining = max(0, int(CLAIM_COOLDOWN_SECONDS - seconds))
    minutes, secs = divmod(remaining, 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _player_card_text(
    player: dict,
    header: str,
    assignment_status: str,
    username: str | None = None,
    footer: str | None = None,
    squad_size: int | None = None,
    max_squad_size: int | None = None,
) -> str:
    """
    Build the formatted claim card using Telegram HTML entities
    """
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    bat_hand = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)
    ovr = overall_rating(bat_level, bowl_level)
    buy_price, sell_price = get_price(ovr)
    value_text = f"B: {format_price(buy_price)} | S: {format_price(sell_price)}"

    title = (
        "┏━━━━━━━━━━━━━━━━━━━━┓\n"
        f"    <b>{_escape(header)}</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"
    )

    # User mention block
    user_display = f"👤 <b>Claimed By:</b> @{username}\n\n" if username else ""

    quote_block = (
        "<blockquote>\n"
        f"🏃 Player: {name} {flag}\n"
        "</blockquote>"
    )

    details_block = (
        "<blockquote>\n"
        f"↳ 📌 Assignment   : {_escape(assignment_status)}\n"
        f"↳ 💰 Claim Reward : +1000 Coins\n"
        f"↳ 🏏 Bat Style  : {bat_hand}\n"
        f"↳ 🎯 Bowl Style : {bowl_style}\n"
        f"↳ ⭐ Level        : 🏏 {bat_level} | 🎯 {bowl_level}\n"
        f"↳ 💎 Value        : {value_text}\n"
        "</blockquote>"
    )

    parts = [
        title,
        "",
        user_display + quote_block,
        details_block,
        "━━━━━━━━━━━━━━━━━━",
    ]

    if footer:
        parts.extend(["", footer])

    if squad_size is not None and max_squad_size is not None:
        parts.extend(["", f"Current Squad Size: {squad_size}/{max_squad_size}"])

    return "\n".join(parts)


async def _claim_attempt_gate(conn, user_id: int):
    row = await conn.fetchrow("""
        SELECT claim_attempt_at,
               EXTRACT(EPOCH FROM (NOW() - claim_attempt_at)) AS elapsed
        FROM users
        WHERE user_id = $1
        FOR UPDATE;
    """, user_id)
    if row and row["claim_attempt_at"] is not None:
        elapsed = float(row["elapsed"] or 0)
        if elapsed < CLAIM_ATTEMPT_COOLDOWN_SECONDS:
            return CLAIM_ATTEMPT_COOLDOWN_SECONDS - elapsed
    await conn.execute("UPDATE users SET claim_attempt_at = NOW() WHERE user_id = $1;", user_id)
    return None


async def _auto_release_pending_claims_once() -> int:
    async def _tx(conn):
        rows = await conn.fetch("""
            SELECT claim_id, user_id, player_id, chat_id, message_id
            FROM player_claims
            WHERE status = 'pending'
              AND claimed_at <= NOW() - INTERVAL '60 seconds'
            FOR UPDATE SKIP LOCKED;
        """)
        if not rows:
            return []

        released_rows = []
        for row in rows:
            player_row = await conn.fetchrow(
                "SELECT * FROM players WHERE player_id = $1;", int(row["player_id"])
            )
            if player_row:
                ovr = overall_rating(
                    int(player_row.get("bat_level") or 0),
                    int(player_row.get("bowl_level") or 0),
                )
                _buy_price, sell_price = get_price(ovr)
                await conn.execute(
                    "UPDATE users SET balance = balance + $1, last_seen_at = NOW() WHERE user_id = $2;",
                    int(sell_price), int(row["user_id"]),
                )

            updated = await conn.execute(
                "UPDATE player_claims SET status = 'released' WHERE claim_id = $1 AND status = 'pending';",
                int(row["claim_id"]),
            )
            if updated.endswith(" 1"):
                released_rows.append({
                    "claim_id": int(row["claim_id"]),
                    "chat_id": int(row["chat_id"]) if row["chat_id"] is not None else None,
                    "message_id": int(row["message_id"]) if row["message_id"] is not None else None,
                })
        return released_rows

    try:
        released_rows = await transaction(_tx)
        for row in released_rows:
            if row["chat_id"] is None or row["message_id"] is None:
                continue
            text = (
                "<b>⏱️ CLAIM EXPIRED</b>\n\n"
                "<b>⏳ You didn't choose Retain or Release within 1 minute.</b>\n\n"
                "<b>🔄 The player was automatically released.</b>"
            )
            try:
                await app.edit_message_text(
                    row["chat_id"], row["message_id"], text, parse_mode="HTML", reply_markup=NO_KEYBOARD
                )
            except Exception:
                try:
                    await app.edit_message_caption(
                        row["chat_id"], row["message_id"], text, parse_mode="HTML", reply_markup=NO_KEYBOARD
                    )
                except Exception as exc:
                    print(f"[claim] Could not update expired claim message claim_id={row['claim_id']}: {exc!r}")
        if released_rows:
            print(f"[claim] Auto-released {len(released_rows)} unanswered claim(s) after 60 seconds.")
        return len(released_rows)
    except Exception as exc:
        print(f"[claim] Auto-release sweep failed: {exc!r}")
        return 0


async def _claim_auto_release_worker():
    while True:
        try:
            await asyncio.sleep(15)
            await _auto_release_pending_claims_once()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[claim] Auto-release worker error: {exc!r}")


def _ensure_claim_auto_release_worker():
    global _CLAIM_AUTO_RELEASE_TASK
    if _CLAIM_AUTO_RELEASE_TASK is None or _CLAIM_AUTO_RELEASE_TASK.done():
        try:
            _CLAIM_AUTO_RELEASE_TASK = asyncio.create_task(_claim_auto_release_worker())
        except RuntimeError:
            _CLAIM_AUTO_RELEASE_TASK = None


async def start_claim_maintenance():
    """Start the claim-expiry worker after the application's event loop exists."""
    _ensure_claim_auto_release_worker()
    await _auto_release_pending_claims_once()


@register("claim")
async def claim_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username") or from_user.get("first_name") or "User"

    print(f"[claim] /claim invoked by user_id={user_id}")

    if not await has_completed_debut(int(user_id)):
        await _safe_claim_send_message(
            chat_id,
            "<b>⚠️ Complete your /debut first to unlock player collection.</b>",
            parse_mode="HTML",
        )
        return

    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, last_seen_at = NOW();
        """,
        user_id, from_user.get("username"), from_user.get("first_name"),
    )

    _ensure_claim_auto_release_worker()

    async def _attempt_tx(conn):
        return await _claim_attempt_gate(conn, int(user_id))

    attempt_remaining = await transaction(_attempt_tx)
    if attempt_remaining is not None:
        remaining_text = f"{max(1, int(attempt_remaining + 0.999))}s"
        await _safe_claim_send_message(
            chat_id,
            f"<b>⏳ Claim cooldown active.</b>\n\n<b>You can use /claim again in {html.escape(remaining_text)}.</b>",
            parse_mode="HTML",
        )
        return

    current_squad = await get_team_squad(user_id) or []
    if len(current_squad) >= MAX_SQUAD_SIZE:
        await _safe_claim_send_message(
            chat_id,
            f"<b>⚠️ Your squad is full ({MAX_SQUAD_SIZE}/{MAX_SQUAD_SIZE}).</b>\n"
            "Sell a player before claiming another one.",
            parse_mode="HTML",
        )
        return

    async def _claim_reservation_tx(conn):
        # Serialize all claim attempts for the same user at the database level.
        # The latest claim check and the reservation are therefore one atomic
        # decision: concurrent requests cannot both earn the same hourly slot.
        await conn.execute("SELECT pg_advisory_xact_lock($1);", int(user_id))
        row = await conn.fetchrow(
            """SELECT EXTRACT(EPOCH FROM (NOW() - claimed_at)) AS elapsed
                 FROM player_claims
                WHERE user_id = $1
                ORDER BY claimed_at DESC LIMIT 1;""",
            int(user_id),
        )
        if row and row["elapsed"] is not None and float(row["elapsed"]) < CLAIM_COOLDOWN_SECONDS:
            return max(0.0, float(row["elapsed"]))
        player = await get_random_claim_player()
        if not player:
            return "no_player"
        claim_row = await conn.fetchrow(
            """INSERT INTO player_claims (user_id, player_id, status, chat_id, message_id)
               VALUES ($1, $2, 'pending', $3, $4) RETURNING *;""",
            int(user_id), int(player["player_id"]), int(chat_id), None,
        )
        updated = await conn.execute(
            "UPDATE users SET balance = balance + 1000, last_seen_at = NOW() WHERE user_id = $1;",
            int(user_id),
        )
        if not updated.endswith(" 1"):
            raise RuntimeError(f"Could not credit claim reward for user_id={user_id}")
        return {"claim": dict(claim_row), "player": player}

    reservation = await transaction(_claim_reservation_tx)
    if isinstance(reservation, (int, float)):
        remaining_text = _format_remaining(float(reservation))
        await _safe_claim_send_message(
            chat_id,
            f"<b>⏳ You've already claimed a player recently!</b>\n\n"
            f"<b>Try again in {html.escape(remaining_text)}.</b>",
            parse_mode="HTML",
        )
        return
    if reservation == "no_player":
        await _safe_claim_send_message(
            chat_id,
            "<b>⚠️ No players available to claim yet.</b>\n"
            "Ask the bot admin to /upload_pl players first.",
            parse_mode="HTML",
        )
        return

    claim = reservation["claim"]
    player = reservation["player"]

    squad = current_squad
    text = _player_card_text(
        player,
        "PLAYER ASSIGNMENT",
        assignment_status="Pending",
        username=username,
        footer="❓ Do you want to assign this player to your squad?",
        squad_size=len(squad),
        max_squad_size=MAX_SQUAD_SIZE,
    )

    keyboard = retain_release_keyboard(claim["claim_id"])
    sent_message = None
    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        sent_message = await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        print(f"[claim] Card image failed ({exc!r}), falling back to a text-only message.")
        sent_message = await _safe_claim_send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

    sent_message_id = None
    if isinstance(sent_message, dict):
        sent_message_id = sent_message.get("message_id")
    else:
        sent_message_id = getattr(sent_message, "id", None)
    if sent_message_id:
        try:
            await execute(
                "UPDATE player_claims SET message_id = $1 WHERE claim_id = $2;",
                int(sent_message_id), int(claim["claim_id"]),
            )
        except Exception as exc:
            print(f"[claim] Could not save claim message_id for auto-expiry claim_id={claim['claim_id']}: {exc!r}")

    print(f"[claim] user_id={user_id} claimed player_id={player['player_id']} ({player['name']}), claim_id={claim['claim_id']}, +1000 coins")


@register_callback("claim_retain")
async def on_claim_retain(callback_query):
    claim_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    username = presser.get("username") or presser.get("first_name") or "User"

    claim = await get_claim(claim_id)
    if not claim or claim["user_id"] != presser["id"]:
        await app.answer_callback_query(callback_query["id"], "This isn't your claim!", show_alert=True)
        return
    if claim["status"] != "pending":
        await app.answer_callback_query(callback_query["id"], "This claim has already been resolved.", show_alert=True)
        return

    claimed_player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", claim["player_id"])
    if claimed_player:
        squad = await get_team_squad(presser["id"]) or []
        already_in_squad = any(int(p.get("player_id") or 0) == int(claimed_player["player_id"]) for p in squad)
        if not already_in_squad and len(squad) >= MAX_SQUAD_SIZE:
            await app.answer_callback_query(
                callback_query["id"],
                "Your squad is full. Sell a player before retaining this claim.",
                show_alert=True,
            )
            return

        if not already_in_squad:
            squad.append(dict(claimed_player))
            await save_team_squad(presser["id"], squad)
            await reset_player_user_stats(int(presser["id"]), int(claimed_player["player_id"]))
            footer = (
                "✅ This player has been successfully added to your collection!"
            )
        else:
            footer = "ℹ️ This player is already in your collection."

        # Only mark the claim resolved after the collection write succeeds.
        # A transient DB error now leaves the claim pending so the user can
        # safely retry instead of losing the player assignment.
        await set_claim_status(claim_id, "retained")
        text = _player_card_text(
            claimed_player,
            "PLAYER ASSIGNED",
            assignment_status="Assigned",
            username=username,
            footer=footer,
            squad_size=len(squad),
            max_squad_size=MAX_SQUAD_SIZE,
        )
    else:
        text = "<b>🤝 Player retained and added to your collection!</b>"

    await app.answer_callback_query(callback_query["id"], "Player retained!")
    if (callback_query.get("message") or {}).get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD)


@register_callback("claim_release")
async def on_claim_release(callback_query):
    claim_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    username = presser.get("username") or presser.get("first_name") or "User"

    claim = await get_claim(claim_id)
    if not claim or claim["user_id"] != presser["id"]:
        await app.answer_callback_query(callback_query["id"], "This isn't your claim!", show_alert=True)
        return
    if claim["status"] != "pending":
        await app.answer_callback_query(callback_query["id"], "This claim has already been resolved.", show_alert=True)
        return

    # Release is a sell-like operation: credit the player's current sell value
    # and resolve the claim in one DB transaction so a double-click cannot
    # grant the reward twice.  The existing response text below is unchanged.
    claimed_player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", claim["player_id"])
    if not claimed_player:
        await app.answer_callback_query(callback_query["id"], "Player could not be found.", show_alert=True)
        return

    ovr = overall_rating(int(claimed_player.get("bat_level") or 0), int(claimed_player.get("bowl_level") or 0))
    _buy_price, sell_price = get_price(ovr)

    async def _release_tx(conn):
        row = await conn.fetchrow(
            "SELECT status FROM player_claims WHERE claim_id = $1 FOR UPDATE;",
            claim_id,
        )
        if not row or row["status"] != "pending":
            return False
        await conn.execute(
            "UPDATE player_claims SET status = 'released' WHERE claim_id = $1;",
            claim_id,
        )
        updated = await conn.execute(
            "UPDATE users SET balance = balance + $1, last_seen_at = NOW() WHERE user_id = $2;",
            int(sell_price), int(presser["id"]),
        )
        if not updated.endswith(" 1"):
            raise RuntimeError(f"Could not credit release reward for user_id={presser['id']}")
        return True

    released = await transaction(_release_tx)
    if not released:
        await app.answer_callback_query(callback_query["id"], "This claim has already been resolved.", show_alert=True)
        return
    if claimed_player:
        footer = "🔄 This player has been released back to the global pool."
        text = _player_card_text(
            claimed_player,
            "PLAYER RELEASED",
            assignment_status="Released",
            username=username,
            footer=footer,
        )
    else:
        text = "<b>🔄 Player released back to the pool.</b>"

    await app.answer_callback_query(callback_query["id"], "Player released.")
    if (callback_query.get("message") or {}).get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD)