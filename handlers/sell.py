from __future__ import annotations

print("sell.py loaded")

import html
import secrets
import string

from handlers.registry import register, register_callback
from app import app
from database.query import execute, fetchrow, transaction
from database.players_repo import get_player
from database.special_players_repo import get_special_player_by_id, display_edition
from database.showcase_players_repo import get_showcase_player
import uuid
from database.squads_repo import get_team_squad, save_team_squad
from utils.user_locks import user_lock
from utils.bounded_dict import BoundedDict
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price
from buttons.sell_buttons import sell_confirm_keyboard, sell_range_confirm_keyboard
from services.player_card import overall_rating
from database.player_user_stats_repo import reset_player_user_stats
from services.card_identity import owned_same_card, player_kind, showcase_card_id_from_player_id

NO_KEYBOARD = {"inline_keyboard": []}

DEFAULT_WARNING = "⚠️ Think carefully — this player will leave your squad permanently."
CANCEL_NOTICE = "❌ Sale cancelled — this player stays in your squad."


# In-memory batch-sale snapshots keep callback data short while preserving the exact
# numbered players shown to the user until the confirmation/cancellation is pressed.
_PENDING_BATCH_SALES: dict[str, dict] = BoundedDict(1000)
_PENDING_SELL_SEARCH: dict[str, dict] = BoundedDict(1000)


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


def _sell_display_name(player: dict) -> str:
    name = str(player.get("name") or "Unknown").strip()
    kind = player_kind(player)
    if kind == "special" and player.get("edition"):
        return f"{name} ({display_edition(player.get('edition'))})"
    if kind == "showcase" and player.get("showcase_name"):
        return f"{name} [{player.get('showcase_name')}]"
    return name


def _owned_player_matches(squad: list[dict], query: str) -> list[dict]:
    q = str(query or "").strip()
    if not q:
        return []
    q_lower = q.casefold()
    matches = []
    for raw in squad:
        player = dict(raw)
        display = _sell_display_name(player)
        base = str(player.get("name") or "").strip()
        edition = str(player.get("edition") or "").strip()
        if q_lower in display.casefold() or q_lower in base.casefold():
            matches.append(player)
            continue
        if edition and q_lower in edition.casefold():
            matches.append(player)
    matches.sort(key=lambda p: (
        0 if _sell_display_name(p).casefold() == q_lower else 1,
        0 if _sell_display_name(p).casefold().startswith(q_lower) else 1,
        int(p.get("player_id") or 0),
    ))
    return matches


def _sell_search_keyboard(token: str, page: int, total: int, player: dict, user_id: int) -> dict:
    pages = max(1, total)
    pid = int(player.get("player_id") or 0)
    return {
        "inline_keyboard": [[
            {"text": "⬅️ Previous", "callback_data": f"sell_search:{token}:prev", "style": "primary"},
            {"text": f"📄 {page + 1}/{pages}", "callback_data": "sell_search:noop", "style": "danger"},
            {"text": "Next ➡️", "callback_data": f"sell_search:{token}:next", "style": "success"},
        ], [
            {"text": "💰 Sell This Player", "callback_data": f"sell_search:{token}:sell:{pid}:{int(user_id)}", "style": "success"},
        ]]}

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
        name = _escape(_sell_display_name(player))
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
    special_block = None
    kind = player_kind(player)
    if kind == "special" and player.get("edition"):
        special_block = f"<blockquote><b>✨ Special ➤ {_escape(display_edition(player.get('edition')))}</b></blockquote>"
    elif kind == "showcase" and player.get("showcase_name"):
        special_block = f"<blockquote><b>🏆 Showcase ➤ {_escape(player.get('showcase_name'))}</b></blockquote>"

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

    parts = [title, name_block, rarity_block]
    if special_block:
        parts.append(special_block)
    parts.extend([details_block, separator, "", notice_line])
    return "\n".join(parts)


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



async def _sell_players_atomic(seller_id: int, player_ids: set[int], credit: int) -> bool:
    """Remove players from the squad and credit coins in ONE transaction.

    The squad row is locked (FOR UPDATE) and ownership is re-checked inside the
    transaction, so a double-pressed confirm button (or a concurrent trade/buy) can
    neither credit the coins twice nor remove players that are already gone, and a
    crash cannot leave a player removed without payment (or paid without removal).
    Squad ``player_id`` values are unique across global/special/showcase cards.
    Returns False, writing nothing, when any of ``player_ids`` is no longer owned.
    """
    import json as _json

    async def _tx(conn):
        row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id = $1 FOR UPDATE;", seller_id)
        if not row:
            return False
        raw = row["squad"]
        squad = _json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else _json.loads(_json.dumps(raw, default=str)))
        owned_ids = {int(p.get("player_id") or 0) for p in squad}
        if not player_ids.issubset(owned_ids):
            return False
        remaining = [p for p in squad if int(p.get("player_id") or 0) not in player_ids]
        await conn.execute(
            "UPDATE team_squads SET squad = $2::jsonb, updated_at = NOW() WHERE user_id = $1;",
            seller_id, _json.dumps(remaining, default=str),
        )
        await conn.execute("UPDATE users SET balance = COALESCE(balance,0) + $1 WHERE user_id = $2;", int(credit), seller_id)
        return True

    async with user_lock(seller_id):
        return bool(await transaction(_tx))


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
        valid_selected = [p for p in selected if player_kind(p) in {"global", "showcase"}]
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

    squad = await get_team_squad(user_id) or []
    matches = _owned_player_matches(squad, name)

    if not matches:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found in your account. Check the spelling.",
            parse_mode="HTML",
        )
        return

    if len(matches) > 1:
        token = uuid.uuid4().hex[:10]
        _PENDING_SELL_SEARCH[token] = {
            "seller_id": int(user_id),
            "players": matches,
            "page": 0,
        }
        player = matches[0]
        keyboard = _sell_search_keyboard(token, 0, len(matches), player, int(user_id))
        await app.send_message(
            chat_id,
            _player_sale_text(player, f"⚠️ Multiple matching players found. Use the pages to choose one."),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        print(f"[sell] user_id={user_id} opened partial-name search token={token} matches={len(matches)} query={name!r}")
        return

    player = matches[0]
    text = _player_sale_text(player)
    keyboard = sell_confirm_keyboard(player["player_id"], user_id)
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

    print(f"[sell] user_id={user_id} opened sale prompt for player_id={player['player_id']} ({_sell_display_name(player)})")



@register_callback("sell_search")
async def on_sell_search(callback_query):
    parts = (callback_query.get("data") or "").split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid sale search.", show_alert=True)
        return
    _, token, action = parts[:3]
    if action == "noop":
        await app.answer_callback_query(callback_query["id"], "Use Previous or Next to browse.")
        return

    state = _PENDING_SELL_SEARCH.get(token)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This player search has expired.", show_alert=True)
        return

    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if user_id != int(state["seller_id"]):
        await app.answer_callback_query(callback_query["id"], "This search is not yours.", show_alert=True)
        return

    players = state["players"]
    current = int(state.get("page", 0))

    if action == "sell":
        if len(parts) != 5:
            await app.answer_callback_query(callback_query["id"], "Invalid player selection.", show_alert=True)
            return
        pid = int(parts[3])
        selected = next((dict(p) for p in players if int(p.get("player_id") or 0) == pid), None)
        if not selected:
            await app.answer_callback_query(callback_query["id"], "This player is no longer available.", show_alert=True)
            return
        _PENDING_SELL_SEARCH.pop(token, None)
        await app.answer_callback_query(callback_query["id"], "Selected.")
        await _update_prompt(callback_query, _player_sale_text(selected), sell_confirm_keyboard(pid, user_id))
        return

    if action == "next":
        current = min(len(players) - 1, current + 1)
    elif action == "prev":
        current = max(0, current - 1)
    else:
        await app.answer_callback_query(callback_query["id"], "Invalid page.", show_alert=True)
        return

    state["page"] = current
    player = dict(players[current])
    keyboard = _sell_search_keyboard(token, current, len(players), player, user_id)
    await _update_prompt(
        callback_query,
        _player_sale_text(player, "⚠️ Multiple matching players found. Use the pages to choose one."),
        keyboard,
    )
    await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")


@register_callback("sell_confirm")
async def on_sell_confirm(callback_query):
    player_id_str, seller_id_str = callback_query["data"].split(":")[1:3]
    seller_id = int(seller_id_str)
    presser = callback_query["from"]

    if int(presser["id"]) != seller_id:
        await app.answer_callback_query(callback_query["id"], "This isn't your sale prompt!", show_alert=True)
        return

    pid = int(player_id_str)
    squad = await get_team_squad(seller_id) or []
    player = next((dict(p) for p in squad if int(p.get("player_id") or 0) == pid), None)
    if not player:
        await app.answer_callback_query(callback_query["id"], "This player is no longer in your squad.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
        return
    kind = player_kind(player)
    if kind == "showcase":
        showcase_id = int(player.get("showcase_card_id") or showcase_card_id_from_player_id(pid) or 0)
        db_player = await get_showcase_player(showcase_id) if showcase_id else None
        if not db_player:
            await app.answer_callback_query(callback_query["id"], "This Showcase card no longer exists.", show_alert=True)
            await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
            return
        player = db_player
    elif kind == "special":
        special_id = int(player.get("special_edition_id") or abs(pid))
        db_player = await get_special_player_by_id(special_id)
        if not db_player:
            await app.answer_callback_query(callback_query["id"], "This player no longer exists.", show_alert=True)
            await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
            return
        player = db_player
    else:
        db_player = await get_player(str(player.get("name") or ""))
        if not db_player:
            await app.answer_callback_query(callback_query["id"], "This player no longer exists.", show_alert=True)
            await _update_prompt(callback_query, "<b>⚠️ This player is no longer available.</b>", NO_KEYBOARD)
            return
        player["player_kind"] = "global"
        player["is_special"] = False
        player["is_showcase"] = False

    still_owned = any(owned_same_card(dict(p), player) for p in squad)
    if not still_owned:
        await app.answer_callback_query(callback_query["id"], "You no longer own this player.", show_alert=True)
        await _update_prompt(callback_query, _player_sale_text(player, "ℹ️ You no longer own this player."), NO_KEYBOARD)
        return

    ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    _buy_price, sell_price = get_price(ovr)

    if not await _sell_players_atomic(seller_id, {pid}, int(sell_price)):
        await app.answer_callback_query(callback_query["id"], "You no longer own this player.", show_alert=True)
        await _update_prompt(callback_query, _player_sale_text(player, "ℹ️ You no longer own this player."), NO_KEYBOARD)
        return
    await reset_player_user_stats(seller_id, pid)

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

    if not await _sell_players_atomic(seller_id, id_set, int(total_sell)):
        _PENDING_BATCH_SALES.pop(token, None)
        await app.answer_callback_query(callback_query["id"], "Your squad changed. Please run the sell range again.", show_alert=True)
        await _update_prompt(callback_query, "<b>⚠️ Your squad changed after this sale list was created. Please run the command again.</b>", NO_KEYBOARD)
        return
    for player in selected:
        await reset_player_user_stats(seller_id, int(player["player_id"]))

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

    pid = int(player_id_str)
    if showcase_card_id_from_player_id(pid) is not None:
        player = await get_showcase_player(showcase_card_id_from_player_id(pid))
    elif pid < 0:
        player = await get_special_player_by_id(abs(pid))
    else:
        row = await fetchrow("SELECT * FROM players WHERE player_id = $1;", pid)
        player = dict(row) if row else None
    await app.answer_callback_query(callback_query["id"], "Cancelled.")
    if player:
        await _update_prompt(callback_query, _player_sale_text(player, CANCEL_NOTICE), NO_KEYBOARD)
    else:
        await _update_prompt(callback_query, f"<b>{CANCEL_NOTICE}</b>", NO_KEYBOARD)
