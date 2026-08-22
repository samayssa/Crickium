from __future__ import annotations

print("buy.py loaded")

import html
import uuid

from handlers.registry import register, register_callback
from app import app
from database.query import fetchrow, transaction
from database.players_repo import get_player
from database.special_players_repo import get_player_variants, get_special_player, split_player_edition, display_edition
from database.squads_repo import get_team_squad, save_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price
from buttons.buy_buttons import buy_confirm_keyboard
from buttons.catalog_buttons import buy_catalog_keyboard
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
_BUY_PAGE_STATE: dict[str, dict] = {}


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip() or None


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
    price_text = f"{buy_price:,}"

    title = "<b>╭━━━〔 PLAYER SHOP 〕━━━╮</b>"
    name_block = f"<blockquote><b>👤 {name} {flag}</b></blockquote>"
    rarity_block = f"<blockquote><b>💎 {rarity}</b></blockquote>"
    special_block = None
    if player.get("is_special") and player.get("edition"):
        special_block = f"<blockquote><b>✨ Special     ➤ {_escape(display_edition(player.get('edition')))}</b></blockquote>"
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
    parts = [title, name_block, rarity_block]
    if special_block:
        parts.append(special_block)
    parts.extend([details_block, separator, "", footer_line])
    return "\n".join(parts)


async def _update_prompt(callback_query: dict, text: str, reply_markup=None) -> None:
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    if (callback_query.get("message") or {}).get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)


async def _attempt_purchase(user_id: int, player: dict, buy_price: int) -> str:
    squad = await get_team_squad(user_id) or []
    already_owned = any(
        int(p.get("player_id") or 0) == int(player["player_id"])
        and bool(p.get("is_special")) == bool(player.get("is_special"))
        for p in squad
    )
    if already_owned:
        return "already_owned"

    async def _tx(conn):
        balance = int(await conn.fetchval("SELECT balance FROM users WHERE user_id = $1;", user_id) or 0)
        if balance < buy_price:
            return "insufficient_balance"
        await conn.execute("UPDATE users SET balance = balance - $1, total_spent = total_spent + $1 WHERE user_id = $2;", buy_price, user_id)
        return "success"

    result = await transaction(_tx)
    if result == "success":
        squad.append(dict(player))
        await save_team_squad(user_id, squad)
        if not player.get("is_special"):
            await reset_player_user_stats(user_id, int(player["player_id"]))
    return result


async def _resolve_buy_player(source: str, player_id: int) -> dict | None:
    if source == "special":
        return await get_special_player_by_id(player_id)
    row = await fetchrow("SELECT * FROM players WHERE player_id = $1;", int(player_id))
    return dict(row) if row else None


from database.special_players_repo import get_special_player_by_id


def _player_buy_callback(player: dict, buyer_id: int) -> str:
    source = "special" if player.get("is_special") else "global"
    entity_id = int(player.get("special_edition_id") if player.get("is_special") else player["player_id"])
    return f"buy_confirm:{source}:{entity_id}:{int(buyer_id)}"


def _player_decline_callback(player: dict, buyer_id: int) -> str:
    source = "special" if player.get("is_special") else "global"
    entity_id = int(player.get("special_edition_id") if player.get("is_special") else player["player_id"])
    return f"buy_decline:{source}:{entity_id}:{int(buyer_id)}"


def _buy_keyboard(player: dict, buyer_id: int):
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    import inspect
    params = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
    def b(text, data, style):
        if "style" in params:
            return InlineKeyboardButton(text, callback_data=data, style=style)
        prefix = "🟢" if style == "success" else "🔴"
        return InlineKeyboardButton(f"{prefix} {text}", callback_data=data)
    return InlineKeyboardMarkup([[b("Yes, Sign", _player_buy_callback(player, buyer_id), "success"), b("Maybe Later", _player_decline_callback(player, buyer_id), "danger")]])


async def _send_buy_card(chat_id: int, user_id: int, player: dict, footer: str = DEFAULT_FOOTER, keyboard=None):
    text = _player_shop_text(player, footer)
    keyboard = keyboard or _buy_keyboard(player, user_id)
    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        return await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        print(f"[buy] Card image failed ({exc!r}), falling back to text-only message.")
        return await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


@register("buy")
async def buy_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await has_completed_debut(user_id):
        await app.send_message(chat_id, "<b>⚠️ Complete your /debut first to unlock player collection.</b>", parse_mode="HTML")
        return

    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(chat_id, "<b>⚠️ Please tell me which player to buy.</b>\nUsage: <code>/buy Virat Kohli</code>", parse_mode="HTML")
        return

    base_name, edition = split_player_edition(name)
    if edition:
        special = await get_special_player(base_name, edition)
        players = [special] if special else []
    else:
        players = await get_player_variants(name)

    if not players:
        await app.send_message(chat_id, f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling, or upload them first with /upload_pl.", parse_mode="HTML")
        return

    if len(players) == 1:
        await _send_buy_card(chat_id, user_id, players[0])
        return

    token = uuid.uuid4().hex[:10]
    _BUY_PAGE_STATE[token] = {"players": players, "owner_id": user_id, "page": 0}
    first = players[0]
    keyboard = buy_catalog_keyboard(token, 0, len(players), _player_buy_callback(first, user_id), _player_decline_callback(first, user_id))
    await _send_buy_card(chat_id, user_id, first, keyboard=keyboard)


@register_callback("buy_page")
async def buy_page_callback(callback_query):
    data = (callback_query.get("data") or "").split(":")
    if len(data) != 3:
        await app.answer_callback_query(callback_query["id"], "Page state expired.", show_alert=True)
        return
    _, token, action = data
    state = _BUY_PAGE_STATE.get(token)
    if not state:
        await app.answer_callback_query(callback_query["id"], "Page state expired. Send /buy again.", show_alert=True)
        return
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if user_id != int(state["owner_id"]):
        await app.answer_callback_query(callback_query["id"], "This shop page is not yours.", show_alert=True)
        return
    players = state["players"]
    current = int(state.get("page", 0))
    if action == "noop":
        await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")
        return
    current = min(len(players)-1, current+1) if action == "next" else max(0, current-1)
    state["page"] = current
    player = players[current]
    message = callback_query["message"]
    chat_id = message["chat"]["id"]
    try:
        await app.delete_message(chat_id, message["message_id"])
    except Exception:
        pass
    keyboard = buy_catalog_keyboard(token, current, len(players), _player_buy_callback(player, user_id), _player_decline_callback(player, user_id))
    await _send_buy_card(chat_id, user_id, player, keyboard=keyboard)
    await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")


@register_callback("buy_confirm")
async def on_buy_confirm(callback_query):
    parts = callback_query["data"].split(":")
    presser = callback_query["from"]
    if len(parts) == 3:
        _, player_id_str, buyer_id_str = parts
        source = "global"
    elif len(parts) == 4:
        _, source, player_id_str, buyer_id_str = parts
    else:
        await app.answer_callback_query(callback_query["id"], "Invalid purchase request.", show_alert=True)
        return
    if int(presser["id"]) != int(buyer_id_str):
        await app.answer_callback_query(callback_query["id"], "This isn't your shop prompt!", show_alert=True)
        return

    player = await _resolve_buy_player(source, int(player_id_str))
    if not player:
        await app.answer_callback_query(callback_query["id"], "This player no longer exists.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
        return

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


@register_callback("buy_decline")
async def on_buy_decline(callback_query):
    parts = callback_query["data"].split(":")
    presser = callback_query["from"]
    if len(parts) == 3:
        _, player_id_str, buyer_id_str = parts
        source = "global"
    elif len(parts) == 4:
        _, source, player_id_str, buyer_id_str = parts
    else:
        await app.answer_callback_query(callback_query["id"], "Invalid shop request.", show_alert=True)
        return
    if int(presser["id"]) != int(buyer_id_str):
        await app.answer_callback_query(callback_query["id"], "This isn't your shop prompt!", show_alert=True)
        return
    player = await _resolve_buy_player(source, int(player_id_str))
    await app.answer_callback_query(callback_query["id"], "Maybe next time!")
    if player:
        await _update_prompt(callback_query, _player_shop_text(player, DECLINE_FOOTER), NO_KEYBOARD)
    else:
        await _update_prompt(callback_query, f"<b>{DECLINE_FOOTER}</b>", NO_KEYBOARD)
