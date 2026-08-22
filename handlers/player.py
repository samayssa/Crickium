from __future__ import annotations

print("player.py loaded")

import html
import uuid

from handlers.registry import register, register_callback
from app import app
from database.players_repo import get_player
from database.special_players_repo import get_player_variants, split_player_edition, display_edition, get_special_player
from database.squads_repo import get_team_squad
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price, format_price
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating
from buttons.catalog_buttons import catalog_page_keyboard

_PLAYER_PAGE_STATE: dict[str, dict] = {}


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _player_bio_text(player: dict, owned: bool) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    role = _escape(player.get("role") or "Unknown")
    bat_style = _escape(batting_style_text(player.get("batting_hand")))
    bowl_style = _escape(bowling_style_text(player.get("bowling_hand")))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)

    ovr = overall_rating(bat_level, bowl_level)
    rarity = _escape(get_rarity(ovr))
    buy_price, _sell_price = get_price(ovr)
    buy_price_text = _escape(format_price(buy_price))
    owned_text = "YES ✅" if owned else "NO ❌"

    title = "<b>╭━━━〔 🏏 PLAYER BIO 〕━━━╮</b>"
    name_block = f"<blockquote>👤 <b>{name}</b> {flag}</blockquote>"
    rarity_block = f"<blockquote>🏅 <b>Rarity.</b>    {rarity}</blockquote>"

    special_block = None
    if player.get("is_special") and player.get("edition"):
        special_block = f"<blockquote>✨ <b>Special</b>    ➤ {_escape(display_edition(player.get('edition')))}</blockquote>"

    details_block = (
        "<blockquote>"
        f"⭐ <b>Role</b>        ➤ {role}\n"
        f"🏏 <b>Bat Style</b>   ➤ {bat_style}\n"
        f"🎯 <b>Bowl Style</b>  ➤ {bowl_style}"
        "</blockquote>"
    )
    separator = "════════════════════"
    stats_block = (
        "<blockquote expandable>"
        f"📈 <b>Bat Lv.</b>     ➤ {bat_level}\n"
        f"📉 <b>Bowl Lv.</b>    ➤ {bowl_level}\n"
        f"💰 <b>Buy Price</b>   ➤ {buy_price_text}\n"
        f"📦 <b>Owned</b>       ➤ {owned_text}"
        "</blockquote>"
    )
    parts = [title, name_block, rarity_block]
    if special_block:
        parts.append(special_block)
    parts.extend(["", details_block, separator, stats_block])
    return "\n".join(parts)


async def _send_player_card(chat_id: int, user_id: int, player: dict, reply_markup=None):
    squad = await get_team_squad(user_id) or []
    owned = any(
        int(p.get("player_id") or 0) == int(player.get("player_id") or 0)
        and bool(p.get("is_special")) == bool(player.get("is_special"))
        for p in squad
    )
    text = _player_bio_text(player, owned)
    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        return await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as exc:
        print(f"[player] Card image failed ({exc!r}), falling back to text-only message.")
        return await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)


def _page_player_variants(players: list[dict], page: int) -> dict:
    return players[max(0, min(page, len(players)-1))]


@register("player")
async def player_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(chat_id, "<b>⚠️ Please tell me which player to look up.</b>\nUsage: <code>/player Virat Kohli</code>", parse_mode="HTML")
        return

    base_name, edition = split_player_edition(name)
    if edition:
        player = await get_special_player(base_name, edition)
        players = [player] if player else []
    else:
        players = await get_player_variants(name)

    if not players:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling, or upload them first with /upload_pl.",
            parse_mode="HTML",
        )
        return

    if len(players) == 1:
        await _send_player_card(chat_id, int(user_id), players[0])
        return

    token = uuid.uuid4().hex[:10]
    _PLAYER_PAGE_STATE[token] = {"players": players, "owner_id": int(user_id)}
    keyboard = catalog_page_keyboard(f"player_page:{token}", 0, len(players))
    await _send_player_card(chat_id, int(user_id), players[0], keyboard)


@register_callback("player_page")
async def player_page_callback(callback_query):
    data = callback_query.get("data", "")
    parts = data.split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Page state expired.", show_alert=True)
        return
    _, token, action = parts
    state = _PLAYER_PAGE_STATE.get(token)
    if not state:
        await app.answer_callback_query(callback_query["id"], "Page state expired. Send /player again.", show_alert=True)
        return
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if user_id != int(state["owner_id"]):
        await app.answer_callback_query(callback_query["id"], "This player page is not yours.", show_alert=True)
        return
    players = state["players"]
    current = int(state.get("page", 0))
    if action == "noop":
        await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")
        return
    if action == "next":
        current = min(len(players) - 1, current + 1)
    elif action == "prev":
        current = max(0, current - 1)
    else:
        await app.answer_callback_query(callback_query["id"], "Invalid page.", show_alert=True)
        return
    state["page"] = current
    player = players[current]
    squad = await get_team_squad(user_id) or []
    owned = any(int(p.get("player_id") or 0) == int(player.get("player_id") or 0) and bool(p.get("is_special")) == bool(player.get("is_special")) for p in squad)
    text = _player_bio_text(player, owned)
    keyboard = catalog_page_keyboard(f"player_page:{token}", current, len(players))
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    # The app wrapper intentionally exposes caption/text edits but not editMessageMedia.
    # Replace the old card with a new message so a special edition can show its own image.
    try:
        await app.delete_message(chat_id, message_id)
    except Exception:
        pass
    await _send_player_card(chat_id, user_id, player, keyboard)
    await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")
