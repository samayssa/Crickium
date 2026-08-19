from __future__ import annotations

print("pshop.py loaded")

import html
import math

from handlers.registry import register, register_callback
from app import app
from database.query import fetch, fetchval
from services.player_card import overall_rating
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from utils.price_chart import get_price

PAGE_SIZE = 5
ROLE_ALIASES = {
    "batsman": "Batsman",
    "batter": "Batsman",
    "bat": "Batsman",
    "bowler": "Bowler",
    "bowlers": "Bowler",
    "bowling": "Bowler",
    "bowl": "Bowler",
    "ball": "Bowler",
    "allrounder": "AllRounder",
    "all-rounder": "AllRounder",
    "all_rounder": "AllRounder",
    "ar": "AllRounder",
    "wicketkeeper": "Wicketkeeper",
    "wicket-keeper": "Wicketkeeper",
    "wicket keeper": "Wicketkeeper",
    "wk": "Wicketkeeper",
}

NO_KEYBOARD = {"inline_keyboard": []}


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_filter(text: str) -> tuple[str | None, str | None, int | None]:
    parts = (text or "").split()
    if len(parts) < 2:
        return None, None, None
    args = parts[1:]
    role = ROLE_ALIASES.get(args[0].strip().lower())
    if role:
        if len(args) == 1:
            return "role", role, None
        try:
            return "role", role, int(args[1])
        except ValueError:
            return None, None, None
    token = args[0].strip()
    try:
        return "level", None, int(token)
    except ValueError:
        rarity = token.strip().lower()
        valid_rarities = {"common", "medium", "rare", "epic", "elite", "legendary", "iconic"}
        if rarity in valid_rarities:
            return "rarity", rarity.title(), None
    return None, None, None


def _where_clause(kind: str, value, level: int | None):
    params = []
    conditions = []
    if kind == "role":
        conditions.append("role = $1")
        params.append(value)
        if level is not None:
            conditions.append("GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) = $2")
            params.append(int(level))
    elif kind == "level":
        conditions.append("GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) = $1")
        params.append(int(level))
    else:
        # Rarity is derived from OVR, so mirror utils.rarity.py exactly.
        ranges = {
            "Common": (0, 64),
            "Medium": (65, 74),
            "Rare": (75, 84),
            "Epic": (85, 89),
            "Elite": (90, 94),
            "Legendary": (95, 97),
            "Iconic": (98, 999),
        }
        low, high = ranges[value]
        conditions.append("GREATEST(COALESCE(bat_level, 0), COALESCE(bowl_level, 0)) BETWEEN $1 AND $2")
        params.extend([low, high])
    return " AND ".join(conditions), params


def _rarity_label(ovr: int) -> str:
    return get_rarity(ovr)


def _role_icon(role: str) -> str:
    return {
        "Batsman": "🏏 Batsman",
        "Bowler": "⚡ Bowler",
        "AllRounder": "🔄 All-Rounder",
        "Wicketkeeper": "🧤 Wicketkeeper",
    }.get(role, f"🏏 {role or 'Player'}")


def _page_keyboard(page: int, total: int, kind: str, value, level: int | None):
    pages = max(1, math.ceil(total / PAGE_SIZE))
    token = f"{kind}|{value or ''}|{level if level is not None else ''}"
    return {
        "inline_keyboard": [[
            {
                "text": "⬅️ Previous",
                "callback_data": f"pshop_page:{max(0, page - 1)}:{token}",
                "style": "primary",
            },
            {
                "text": f"📄 {page + 1}/{pages}",
                "callback_data": "pshop_page:noop",
                "style": "danger",
            },
            {
                "text": "Next ➡️",
                "callback_data": f"pshop_page:{min(pages - 1, page + 1)}:{token}",
                "style": "success",
            },
        ]]
    }


def _render(players: list[dict], page: int, total: int, filter_text: str) -> str:
    lines = ["<b>╭━━〔 🛒 PLAYER SHOP 〕━━╮</b>", ""]
    if not players:
        lines += [f"<b>No players found for {html.escape(filter_text)}.</b>", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
        return "\n".join(lines)
    for index, player in enumerate(players):
        name = _escape(player.get("name") or "Unknown")
        flag = flag_for(player.get("country"))
        ovr = overall_rating(player.get("bat_level"), player.get("bowl_level"))
        rarity = _rarity_label(ovr)
        price, _ = get_price(ovr)
        lines.extend([
            f"<b>👤 {name} {flag}</b>",
            f"<b>⭐ OVR: {ovr}  |  💎 {html.escape(rarity)}</b>",
            f"<b>💰 Price: 🪙 {price:,}</b>",
            f"<b>      ╰➤ {_role_icon(str(player.get('role') or 'Player'))}</b>",
        ])
        if index != len(players) - 1:
            lines.append("")
            lines.append("<b>────────────────────</b>")
            lines.append("")
    lines += ["", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines)


async def _fetch_page(kind: str, value, level: int | None, page: int):
    where, params = _where_clause(kind, value, level)
    total = int(await fetchval(f"SELECT COUNT(*) FROM players WHERE {where};", *params) or 0)
    order_sql = "ORDER BY player_id ASC"
    offset = max(0, page) * PAGE_SIZE
    rows = await fetch(
        f"SELECT * FROM players WHERE {where} {order_sql} OFFSET ${len(params)+1} LIMIT ${len(params)+2};",
        *params, offset, PAGE_SIZE,
    )
    return [dict(r) for r in rows], total


@register("pshop")
async def pshop_command(message):
    chat_id = message["chat"]["id"]
    kind, value, level = _parse_filter(message.get("text", ""))
    if kind is None:
        await app.send_message(
            chat_id,
            "<b>⚠️ Usage: /pshop 78, /pshop batsman, /pshop batsman 85, /pshop bowler 78, or /pshop legendary.</b>",
            parse_mode="HTML",
        )
        return

    players, total = await _fetch_page(kind, value, level, 0)
    filter_text = str(level) if kind == "level" else (f"{value} {level}" if kind == "role" else str(value))
    await app.send_message(
        chat_id,
        _render(players, 0, total, filter_text),
        parse_mode="HTML",
        reply_markup=_page_keyboard(0, total, kind, value, level),
    )


@register_callback("pshop_page")
async def pshop_page(callback_query):
    data = callback_query.get("data", "")
    parts = data.split(":", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid page.", show_alert=True)
        return
    if parts[1] == "noop":
        await app.answer_callback_query(callback_query["id"], "This is the current page.")
        return
    page = int(parts[1])
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Page state expired. Send /pshop again.", show_alert=True)
        return
    token_parts = parts[2].split("|", 2)
    if len(token_parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Page state expired. Send /pshop again.", show_alert=True)
        return
    kind, value, level_raw = token_parts
    level = int(level_raw) if level_raw else None
    # For level filters, value is empty. For role filters, value is the role.
    players, total = await _fetch_page(kind, value or None, level, page)
    filter_text = str(level) if kind == "level" else (f"{value} {level}" if kind == "role" else str(value))
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    if page >= pages:
        page = pages - 1
        players, total = await _fetch_page(kind, value or None, level, page)
    await app.answer_callback_query(callback_query["id"], f"Page {page + 1}/{pages}")
    await app.edit_message_text(
        chat_id,
        message_id,
        _render(players, page, total, filter_text),
        parse_mode="HTML",
        reply_markup=_page_keyboard(page, total, kind, value or None, level),
    )
