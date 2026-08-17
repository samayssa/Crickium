from __future__ import annotations

print("buy.py loaded")

import html

from handlers.registry import register, register_callback
from app import app
from database.query import fetchrow, transaction
from database.players_repo import get_player
from database.squads_repo import get_team_squad, save_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price
from buttons.buy_buttons import buy_confirm_keyboard
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating
from database.player_user_stats_repo import reset_player_user_stats
from utils.debut_gate import has_completed_debut

NO_KEYBOARD = {"inline_keyboard": []}

DEFAULT_FOOTER = "🛒 Ready to sign this player?"
SUCCESS_FOOTER = "🥳 Yayy! You invested in the right player."
INSUFFICIENT_BALANCE_FOOTER = "❌ You don't have enough balance to buy this player."
ALREADY_OWNED_FOOTER = "ℹ️ You already own this player."
DECLINE_FOOTER = "🕐 Maybe next time!"


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _player_shop_text(player: dict, footer: str = DEFAULT_FOOTER) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    bat_style = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)

    ovr = overall_rating(bat_level, bowl_level)
    rarity = _escape(get_rarity(ovr))
    buy_price, _sell_price = get_price(ovr)
    # The requested template shows the full comma-separated number
    # (e.g. "7,250,000"), not the abbreviated "7.2M" that
    # utils.price_chart.format_price() produces elsewhere (player.py,
    # claim.py) - so this formats it directly instead of reusing that
    # helper, without changing format_price() itself.
    price_text = f"{buy_price:,}"

    title = "<b>╭━━━〔 PLAYER SHOP 〕━━━╮</b>"

    name_block = f"<blockquote><b>👤 {name} {flag}</b></blockquote>"

    rarity_block = f"<blockquote><b>💎 {rarity}</b></blockquote>"

    details_block = (
        "<blockquote><b><i>"
        f"├ ⚡ Bat Lv.     : {bat_level}\n"
        f"├ 🎯 Ball Lv.    : {bowl_level}\n"
        f"├ 🏏 Batting   : {bat_style}\n"
        f"├ 🥎 Bowling  : {bowl_style}\n"
        f"╰ 💰 Price       : 🪙 {price_text}"
        "</i></b></blockquote>"
    )

    separator = "<b>════════════════════</b>"

    footer_line = f"<b>{_escape(footer)}</b>"

    return "\n".join([title, name_block, rarity_block, details_block, separator, "", footer_line])


async def _update_prompt(callback_query: dict, text: str, reply_markup=None) -> None:
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    if (callback_query.get("message") or {}).get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)


async def _attempt_purchase(user_id: int, player: dict, buy_price: int) -> str:
    """
    Tries to buy `player` for `user_id`. Returns one of:
    "already_owned", "insufficient_balance", "success".
    Balance deduction + squad addition happen atomically together.
    """
    squad = await get_team_squad(user_id) or []
    already_owned = any(int(p.get("player_id") or 0) == int(player["player_id"]) for p in squad)
    if already_owned:
        return "already_owned"

    async def _tx(conn):
        balance = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1;", user_id)
        balance = int(balance or 0)
        if balance < buy_price:
            return "insufficient_balance"

        await conn.execute(
            "UPDATE users SET balance = balance - $1, total_spent = total_spent + $1 WHERE user_id = $2;",
            buy_price, user_id,
        )
        return "success"

    result = await transaction(_tx)

    if result == "success":
        squad.append(dict(player))
        await save_team_squad(user_id, squad)
        await reset_player_user_stats(user_id, int(player["player_id"]))

    return result


@register("buy")
async def buy_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[buy] /buy invoked by user_id={user_id}")

    if not await has_completed_debut(int(user_id)):
        await app.send_message(
            chat_id,
            "<b>⚠️ Complete your /debut first to unlock player collection.</b>",
            parse_mode="HTML",
        )
        return

    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please tell me which player to buy.</b>\nUsage: <code>/buy Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    player = await get_player(name)
    if not player:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling, "
            f"or upload them first with /upload_pl.",
            parse_mode="HTML",
        )
        return

    text = _player_shop_text(player)
    keyboard = buy_confirm_keyboard(player["player_id"], user_id)

    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        print(f"[buy] Card image failed ({exc!r}), falling back to a text-only message.")
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

    print(f"[buy] user_id={user_id} opened shop prompt for player_id={player['player_id']} ({player['name']})")


@register_callback("buy_confirm")
async def on_buy_confirm(callback_query):
    player_id_str, buyer_id_str = callback_query["data"].split(":")[1:3]
    presser = callback_query["from"]

    if int(presser["id"]) != int(buyer_id_str):
        await app.answer_callback_query(callback_query["id"], "This isn't your shop prompt!", show_alert=True)
        return

    player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", int(player_id_str))
    if not player:
        await app.answer_callback_query(callback_query["id"], "This player no longer exists.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
        return
    player = dict(player)

    ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    buy_price, _sell_price = get_price(ovr)

    result = await _attempt_purchase(int(buyer_id_str), player, buy_price)

    if result == "already_owned":
        await app.answer_callback_query(callback_query["id"], "You already own this player!")
        await _update_prompt(callback_query, _player_shop_text(player, ALREADY_OWNED_FOOTER), NO_KEYBOARD)
    elif result == "insufficient_balance":
        await app.answer_callback_query(callback_query["id"], "Not enough balance!", show_alert=True)
        await _update_prompt(callback_query, _player_shop_text(player, INSUFFICIENT_BALANCE_FOOTER), NO_KEYBOARD)
    else:
        await app.answer_callback_query(callback_query["id"], "Signed!")
        await _update_prompt(callback_query, _player_shop_text(player, SUCCESS_FOOTER), NO_KEYBOARD)
        print(f"[buy] user_id={buyer_id_str} bought player_id={player['player_id']} ({player['name']}) for {buy_price}")


@register_callback("buy_decline")
async def on_buy_decline(callback_query):
    player_id_str, buyer_id_str = callback_query["data"].split(":")[1:3]
    presser = callback_query["from"]

    if int(presser["id"]) != int(buyer_id_str):
        await app.answer_callback_query(callback_query["id"], "This isn't your shop prompt!", show_alert=True)
        return

    player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", int(player_id_str))
    await app.answer_callback_query(callback_query["id"], "Maybe next time!")
    if player:
        await _update_prompt(callback_query, _player_shop_text(dict(player), DECLINE_FOOTER), NO_KEYBOARD)
    else:
        await _update_prompt(callback_query, f"<b>{DECLINE_FOOTER}</b>", NO_KEYBOARD)
