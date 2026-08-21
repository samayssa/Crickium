from __future__ import annotations

print("claim.py loaded")

import html

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
MAX_SQUAD_SIZE = 25
NO_KEYBOARD = {"inline_keyboard": []}


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


@register("claim")
async def claim_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username") or from_user.get("first_name") or "User"

    print(f"[claim] /claim invoked by user_id={user_id}")

    if not await has_completed_debut(int(user_id)):
        await app.send_message(
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

    elapsed = await seconds_since_last_claim(user_id)
    if elapsed is not None and elapsed < CLAIM_COOLDOWN_SECONDS:
        remaining_text = _format_remaining(elapsed)
        await app.send_message(
            chat_id,
            f"<b>⏳ You've already claimed a player recently!</b>\n\n"
            f"<b>Try again in {html.escape(remaining_text)}.</b>",
            parse_mode="HTML",
        )
        return

    current_squad = await get_team_squad(user_id) or []
    if len(current_squad) >= MAX_SQUAD_SIZE:
        await app.send_message(
            chat_id,
            f"<b>⚠️ Your squad is full ({MAX_SQUAD_SIZE}/{MAX_SQUAD_SIZE}).</b>\n"
            "Sell a player before claiming another one.",
            parse_mode="HTML",
        )
        return

    player = await get_random_claim_player()
    if not player:
        await app.send_message(
            chat_id,
            "<b>⚠️ No players available to claim yet.</b>\n"
            "Ask the bot admin to /upload_pl players first.",
            parse_mode="HTML",
        )
        return

    # Create the claim and credit its reward in one transaction so a DB error
    # cannot leave a pending claim without its coins, or vice versa.
    async def _claim_tx(conn):
        claim_row = await conn.fetchrow(
            """
            INSERT INTO player_claims (user_id, player_id, status)
            VALUES ($1, $2, 'pending')
            RETURNING *;
            """,
            user_id, player["player_id"],
        )
        updated = await conn.execute(
            "UPDATE users SET balance = balance + 1000 WHERE user_id = $1;",
            user_id,
        )
        if not updated.endswith(" 1"):
            raise RuntimeError(f"Could not credit claim reward for user_id={user_id}")
        return dict(claim_row)

    claim = await transaction(_claim_tx)

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
    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        print(f"[claim] Card image failed ({exc!r}), falling back to a text-only message.")
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

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