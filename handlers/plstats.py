from __future__ import annotations

print("plstats.py loaded")

import html
import uuid

from handlers.registry import register, register_callback
from app import app
from database.players_repo import get_player
from database.special_players_repo import search_player_variants, split_player_edition, get_special_player
from database.squads_repo import get_team_squad
from database.player_user_stats_repo import get_player_user_stats
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _num(value) -> str:
    """Formats a NUMERIC(8,2) DB value without a pointless '.00'."""
    try:
        f = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{f:.0f}" if f == int(f) else f"{f:.2f}"


def _plstats_text(player: dict, stats: dict) -> str:
    name = _escape(player.get("name") or "Unknown")
    if player.get("is_special") and player.get("edition"):
        name = f"{name} ({_escape(player.get('edition'))})"
    flag = flag_for(player.get("country"))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)
    rarity = _escape(get_rarity(overall_rating(bat_level, bowl_level)))

    bat_matches = int(stats.get("bat_matches") or 0)
    bat_innings = int(stats.get("bat_innings") or 0)
    runs = int(stats.get("runs") or 0)
    fifties = int(stats.get("fifties") or 0)
    centuries = int(stats.get("centuries") or 0)
    bat_balls = int(stats.get("bat_balls") or 0)
    dismissals = int(stats.get("dismissals") or 0)
    bat_average = (runs / dismissals) if dismissals else (0.0 if runs == 0 else float(runs))
    strike_rate = (runs / bat_balls * 100.0) if bat_balls else 0.0

    bowl_matches = int(stats.get("bowl_matches") or 0)
    bowl_innings = int(stats.get("bowl_innings") or 0)
    wickets = int(stats.get("wickets") or 0)
    three_wickets = int(stats.get("three_wickets") or 0)
    five_wickets = int(stats.get("five_wickets") or 0)
    bowl_balls = int(stats.get("bowl_balls") or 0)
    bowl_runs = int(stats.get("bowl_runs") or 0)
    bowl_average = (bowl_runs / wickets) if wickets else 0.0
    economy = (bowl_runs / bowl_balls * 6.0) if bowl_balls else 0.0

    def fmt(value: float) -> str:
        return str(int(value)) if value == int(value) else f"{value:.2f}"

    return (
        "<b>╭━━〔 PLAYER STATS 〕━━╮</b>\n\n"
        f"<blockquote>👤 <b>{name}</b>  {flag}</blockquote>\n\n"
        f"<blockquote>🏅 <b>Rarity:</b> {rarity}</blockquote>\n\n"
        f"<blockquote>⚡ <b>Level:</b> ➤ {overall_rating(bat_level, bowl_level)}</blockquote>\n\n"
        f"<blockquote expandable><b>🏏 BATTING OVERVIEW LV {bat_level}</b>\n\n"
        f"├ 🎮 Matches       : {bat_matches}\n"
        f"├ 🏏 Innings       : {bat_innings}\n"
        f"├ 🔥 Runs          : {runs}\n"
        f"├ 💯 50s / 100s    : {fifties} / {centuries}\n"
        f"├ 📊 Average       : {fmt(bat_average)}\n"
        f"╰ ⚡ Strike Rate   : {fmt(strike_rate)}\n"
        "</blockquote>\n"
        "<b>════════════════════</b>\n\n"
        f"<blockquote expandable><b>🎯 BOWLING OVERVIEW LV {bowl_level}</b>\n\n"
        f"├ 🎮 Matches       : {bowl_matches}\n"
        f"├ 🎯 Innings       : {bowl_innings}\n"
        f"├ 🔥 Wickets       : {wickets}\n"
        f"├ 🏅 3W / 5W       : {three_wickets} / {five_wickets}\n"
        f"├ 📊 Average       : {fmt(bowl_average)}\n"
        f"╰ ⚡ Economy        : {fmt(economy)}\n"
        "</blockquote>\n"
        "<b>╰━━━━━━━━━━━━━━━━━━╯</b>"
    )


@register("plstats")
async def plstats_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please tell me which player to look up.</b>\nUsage: <code>/plstats Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    base_name, edition = split_player_edition(name)
    if edition:
        p = await get_special_player(base_name, edition)
        players = [p] if p else []
    else:
        players = await search_player_variants(name, limit=100)

    # PLStats is ownership-aware: only cards currently held by this user are eligible.
    squad = await get_team_squad(user_id) or []
    owned_ids = {
        (int(p.get("player_id") or 0), bool(p.get("is_special")))
        for p in squad
        if p.get("player_id") is not None
    }
    players = [p for p in players if (int(p.get("player_id") or 0), bool(p.get("is_special"))) in owned_ids]

    if not players:
        await app.send_message(
            chat_id,
            f"⚠️ <b>No player named {_escape(name)} found in your squad.</b>",
            parse_mode="HTML",
        )
        return

    # Exact full name without multiple same-name variants keeps the original one-card flow.
    if len(players) == 1:
        player = players[0]
        stats = await get_player_user_stats(user_id, int(player["player_id"]))
        text = _plstats_text(player, stats)
        try:
            image_bytes, _is_custom = await get_player_card_bytes(player)
            await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML")
        except Exception:
            await app.send_message(chat_id, text, parse_mode="HTML")
        return

    token = uuid.uuid4().hex[:10]
    _PLSTATS_PAGE_STATE[token] = {"players": players, "owner_id": int(user_id), "page": 0}
    await _send_plstats_page(chat_id, int(user_id), token)


async def _send_plstats_page(chat_id: int, user_id: int, token: str) -> None:
    state = _PLSTATS_PAGE_STATE.get(token)
    if not state:
        return
    players = state["players"]
    page = int(state.get("page", 0))
    player = players[page]
    stats = await get_player_user_stats(user_id, int(player["player_id"]))
    text = _plstats_text(player, stats)
    keyboard = catalog_page_keyboard(f"plstats_page:{token}", page, len(players))
    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        # Always send the first page as a fresh message; callbacks edit it in place.
        if not state.get("message_id"):
            msg = await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
            state["message_id"] = int(msg.get("message_id") or msg["message_id"]) if isinstance(msg, dict) else int(getattr(msg, "id", 0))
            state["chat_id"] = int(chat_id)
        else:
            await app.edit_message_media(chat_id, state["message_id"], image_bytes, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        if not state.get("message_id"):
            msg = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
            state["message_id"] = int(msg.get("message_id") or msg["message_id"]) if isinstance(msg, dict) else int(getattr(msg, "id", 0))
            state["chat_id"] = int(chat_id)
        else:
            await app.edit_message_caption(chat_id, state["message_id"], text, parse_mode="HTML", reply_markup=keyboard)


@register_callback("plstats_page")
async def plstats_page_callback(callback_query):
    data = callback_query.get("data", "")
    parts = data.split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Page state expired.", show_alert=True)
        return
    _, token, action = parts
    state = _PLSTATS_PAGE_STATE.get(token)
    if not state:
        await app.answer_callback_query(callback_query["id"], "Page state expired. Send /plstats again.", show_alert=True)
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
    # Keep the same message and edit its media/caption in place.
    await _send_plstats_page(callback_query["message"]["chat"]["id"], user_id, token)
    await app.answer_callback_query(callback_query["id"], f"Page {current + 1}/{len(players)}")
