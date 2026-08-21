from __future__ import annotations

print("sell.py loaded")

import html
import secrets
import string

from handlers.registry import register, register_callback
from app import app
from database.query import execute, fetchrow
from database.players_repo import get_player
from database.squads_repo import get_team_squad, save_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price
from buttons.sell_buttons import sell_confirm_keyboard, sell_range_confirm_keyboard
from services.player_card import overall_rating
from database.player_user_stats_repo import reset_player_user_stats

NO_KEYBOARD = {"inline_keyboard": []}

DEFAULT_WARNING = "⚠️ Think carefully — this player will leave your squad permanently."
CANCEL_NOTICE = "❌ Sale cancelled — this player stays in your squad."


# In-memory batch-sale snapshots keep callback data short while preserving the exact
# numbered players shown to the user until the confirmation/cancellation is pressed.
_PENDING_BATCH_SALES: dict[str, dict] = {}


def _new_sale_token() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        token = "".join(secrets.choice(alphabet) for _ in range(10))
        if token not in _PENDING_BATCH_SALES:
            return token


def _player_role_text(player: dict) -> str:
    role = str(player.get("role") or "").strip()
    if role:
        return role
    if player.get("is_wicketkeeper"):
        return "Wicketkeeper"
    return "Player"


def _parse_numeric_range(text: str) -> tuple[int, int] | None:
    parts = (text or "").split()
    if len(parts) != 3:
        return None
    try:
        start, end = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if start < 1 or end < 1 or start > end:
        return None
    return start, end


def _batch_sale_text(players: list[dict], start: int, end: int, total_sell: int) -> str:
    title = "<b>╭━━━━━━〔 💰 PLAYER SALE 〕━━━━━━╮</b>"
    range_block = f"<b>📋 Players {start}-{end} selected for sale</b>"
    lines = [
        title,
        range_block,
        "",
        "<blockquote><b>",
    ]
    for idx, player in enumerate(players, start=start):
        name = _escape(player.get("name") or "Unknown")
        role = _escape(_player_role_text(player))
        bat_level = int(player.get("bat_level") or 0)
        bowl_level = int(player.get("bowl_level") or 0)
        ovr = overall_rating(bat_level, bowl_level)
        _buy_price, sell_price = get_price(ovr)
        flag = flag_for(player.get("country"))
        connector = "╰" if idx == end else "├"
        lines.append(
            f"{connector} {idx}. 👤 {name} {flag}\n"
            f"   ├ 🏷️ Role    : {role}\n"
            f"   ├ 🏏 Bat Lv. : {bat_level}\n"
            f"   ├ 🎯 Ball Lv.: {bowl_level}\n"
            f"   ╰ 💰 Sell   : 🪙 {sell_price:,}"
        )
    lines += [
        "</b></blockquote>",
        "",
        "<blockquote><b>💰 Total Selling Value ➤ 🪙 " + f"{total_sell:,}" + "</b></blockquote>",
        "",
        "<b>⚠️ Think carefully — these players will leave your squad permanently.</b>",
    ]
    return "\n".join(lines)


def _batch_sold_text(players: list[dict], total_sell: int) -> str:
    title = "<b>╭━━━━〔 ✅ PLAYERS SOLD 〕━━━━╮</b>"
    lines = [title, "", "<blockquote><b>"]
    for player in players:
        name = _escape(player.get("name") or "Unknown")
        lines.append(f"• 👤 {name} ✅")
    lines += [
        "</b></blockquote>",
        "",
        f"<blockquote><b>💰 Total Received ➤ 🪙 {total_sell:,}\n\n🏦 Added to your bank balance.</b></blockquote>",
        "<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>",
    ]
    return "\n".join(lines)


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _player_sale_text(player: dict, notice: str = DEFAULT_WARNING) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    bat_style = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)

    ovr = overall_rating(bat_level, bowl_level)
    rarity = _escape(get_rarity(ovr))
    buy_price, sell_price = get_price(ovr)
    value_text = f"{buy_price:,}"
    return_text = f"{sell_price:,}"

    title = "<b>╭━━━━━━〔 💰 PLAYER SALE 〕━━━━━━╮</b>"

    name_block = f"<blockquote><b>👤 {name} {flag}</b></blockquote>"

    rarity_block = f"<blockquote><b>💎 {rarity}</b></blockquote>"

    details_block = (
        "<blockquote><b><i>"
        f"├ 🏏 Bat Lv. : {bat_level}\n"
        f"├ 🎯 Ball Lv.: {bowl_level}\n"
        f"├ ⚡ Bat     : {bat_style}\n"
        f"├ 🥎 Bowl    : {bowl_style}\n"
        f"├ 💵 Value   : 🪙 {value_text}\n"
        f"╰ 💰 Return  : 🪙 {return_text} (45%)"
        "</i></b></blockquote>"
    )

    separator = "<b>══════════════════════════</b>"

    notice_line = f"<b>{_escape(notice)}</b>"

    return "\n".join([title, name_block, rarity_block, details_block, separator, "", notice_line])


def _player_sold_text(player: dict, sell_price: int) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    return_text = f"{sell_price:,}"

    title = "<b>╭━━━━〔 ✅ PLAYER SOLD 〕━━━━╮</b>"
    name_block = f"<blockquote><b>👤 {name} {flag}</b></blockquote>"
    details_block = (
        "<blockquote><b>"
        f"💰 Received ➤ 🪙 {return_text}\n\n"
        "🏦 Added to your bank balance."
        "</b></blockquote>"
    )
    bottom = "<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>"

    return "\n".join([title, name_block, details_block, bottom])


async def _update_prompt(callback_query: dict, text: str, reply_markup=None) -> None:
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    if (callback_query.get("message") or {}).get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=reply_markup)


@register("sell")
async def sell_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[sell] /sell invoked by user_id={user_id}")

    range_args = _parse_numeric_range(message.get("text", ""))
    if range_args:
        start, end = range_args
        squad = await get_team_squad(user_id) or []
        if end > len(squad):
            await app.send_message(
                chat_id,
                f"⚠️ Your squad currently has only <b>{len(squad)}</b> players. Please use a valid range.",
                parse_mode="HTML",
            )
            return

        selected = [dict(p) for p in squad[start - 1:end]]
        valid_selected = [p for p in selected if int(p.get("player_id") or 0) > 0]
        if not valid_selected:
            await app.send_message(chat_id, "⚠️ No valid players were found in that range.", parse_mode="HTML")
            return

        total_sell = 0
        for player in valid_selected:
            ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
            _buy_price, sell_price = get_price(ovr)
            total_sell += int(sell_price)

        token = _new_sale_token()
        _PENDING_BATCH_SALES[token] = {
            "seller_id": int(user_id),
            "start": start,
            "end": end,
            "player_ids": [int(p["player_id"]) for p in valid_selected],
        }
        await app.send_message(
            chat_id,
            _batch_sale_text(valid_selected, start, end, total_sell),
            parse_mode="HTML",
            reply_markup=sell_range_confirm_keyboard(token),
        )
        print(f"[sell] user_id={user_id} opened batch sale prompt token={token} for positions {start}-{end}")
        return

    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please tell me which player to sell.</b>\nUsage: <code>/sell Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    player = await get_player(name)
    if not player:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling.",
            parse_mode="HTML",
        )
        return

    squad = await get_team_squad(user_id) or []
    owned = any(int(p.get("player_id") or 0) == int(player["player_id"]) for p in squad)
    if not owned:
        await app.send_message(
            chat_id,
            f"⚠️ You don't own <b>{_escape(player['name'])}</b>, so you can't sell them.",
            parse_mode="HTML",
        )
        return

    text = _player_sale_text(player)
    keyboard = sell_confirm_keyboard(player["player_id"], user_id)
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

    print(f"[sell] user_id={user_id} opened sale prompt for player_id={player['player_id']} ({player['name']})")


@register_callback("sell_confirm")
async def on_sell_confirm(callback_query):
    player_id_str, seller_id_str = callback_query["data"].split(":")[1:3]
    seller_id = int(seller_id_str)
    presser = callback_query["from"]

    if int(presser["id"]) != seller_id:
        await app.answer_callback_query(callback_query["id"], "This isn't your sale prompt!", show_alert=True)
        return

    player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", int(player_id_str))
    if not player:
        await app.answer_callback_query(callback_query["id"], "This player no longer exists.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
        return
    player = dict(player)

    squad = await get_team_squad(seller_id) or []
    still_owned = any(int(p.get("player_id") or 0) == int(player["player_id"]) for p in squad)
    if not still_owned:
        await app.answer_callback_query(callback_query["id"], "You no longer own this player.", show_alert=True)
        await _update_prompt(callback_query, _player_sale_text(player, "ℹ️ You no longer own this player."), NO_KEYBOARD)
        return

    ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    _buy_price, sell_price = get_price(ovr)

    new_squad = [p for p in squad if int(p.get("player_id") or 0) != int(player["player_id"])]
    await save_team_squad(seller_id, new_squad)
    await reset_player_user_stats(seller_id, int(player["player_id"]))
    await execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2;", sell_price, seller_id)

    await app.answer_callback_query(callback_query["id"], "Sold!")
    await _update_prompt(callback_query, _player_sold_text(player, sell_price), NO_KEYBOARD)
    print(f"[sell] user_id={seller_id} sold player_id={player['player_id']} ({player['name']}) for {sell_price}")


@register_callback("sell_range_confirm")
async def on_sell_range_confirm(callback_query):
    token = callback_query["data"].split(":", 1)[1]
    pending = _PENDING_BATCH_SALES.get(token)
    presser = callback_query.get("from", {})
    if not pending:
        await app.answer_callback_query(callback_query["id"], "This sale prompt has expired.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ This batch sale prompt has expired.</b>", NO_KEYBOARD)
        return

    seller_id = int(pending["seller_id"])
    if int(presser.get("id") or 0) != seller_id:
        await app.answer_callback_query(callback_query["id"], "This isn't your sale prompt!", show_alert=True)
        return

    squad = await get_team_squad(seller_id) or []
    id_set = set(int(pid) for pid in pending["player_ids"])
    selected = [dict(p) for p in squad if int(p.get("player_id") or 0) in id_set]
    if not selected:
        _PENDING_BATCH_SALES.pop(token, None)
        await app.answer_callback_query(callback_query["id"], "None of these players are still in your squad.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ The selected players are no longer available.</b>", NO_KEYBOARD)
        return

    current_ids = {int(p.get("player_id") or 0) for p in selected}
    if current_ids != id_set:
        _PENDING_BATCH_SALES.pop(token, None)
        await app.answer_callback_query(callback_query["id"], "Your squad changed. Please run the sell range again.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ Your squad changed after this sale list was created. Please run the command again.</b>", NO_KEYBOARD)
        return

    total_sell = 0
    for player in selected:
        ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
        _buy_price, sell_price = get_price(ovr)
        total_sell += int(sell_price)

    new_squad = [p for p in squad if int(p.get("player_id") or 0) not in id_set]
    await save_team_squad(seller_id, new_squad)
    for player in selected:
        await reset_player_user_stats(seller_id, int(player["player_id"]))
    await execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2;", total_sell, seller_id)

    _PENDING_BATCH_SALES.pop(token, None)
    await app.answer_callback_query(callback_query["id"], "Players sold!")
    await _update_prompt(callback_query, _batch_sold_text(selected, total_sell), NO_KEYBOARD)
    print(f"[sell] user_id={seller_id} sold {len(selected)} players in batch token={token} for {total_sell}")


@register_callback("sell_range_cancel")
async def on_sell_range_cancel(callback_query):
    token = callback_query["data"].split(":", 1)[1]
    pending = _PENDING_BATCH_SALES.get(token)
    presser = callback_query.get("from", {})
    if not pending:
        await app.answer_callback_query(callback_query["id"], "This sale prompt has expired.", show_alert=True)
        return
    if int(presser.get("id") or 0) != int(pending["seller_id"]):
        await app.answer_callback_query(callback_query["id"], "This isn't your sale prompt!", show_alert=True)
        return
    _PENDING_BATCH_SALES.pop(token, None)
    await app.answer_callback_query(callback_query["id"], "Cancelled.")
    await _update_prompt(callback_query, "<b>❌ Sale cancelled — these players stay in your squad.</b>", NO_KEYBOARD)


@register_callback("sell_cancel")
async def on_sell_cancel(callback_query):
    player_id_str, seller_id_str = callback_query["data"].split(":")[1:3]
    presser = callback_query["from"]

    if int(presser["id"]) != int(seller_id_str):
        await app.answer_callback_query(callback_query["id"], "This isn't your sale prompt!", show_alert=True)
        return

    player = await fetchrow("SELECT * FROM players WHERE player_id = $1;", int(player_id_str))
    await app.answer_callback_query(callback_query["id"], "Cancelled.")
    if player:
        await _update_prompt(callback_query, _player_sale_text(dict(player), CANCEL_NOTICE), NO_KEYBOARD)
    else:
        await _update_prompt(callback_query, f"<b>{CANCEL_NOTICE}</b>", NO_KEYBOARD)
