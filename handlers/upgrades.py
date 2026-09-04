from __future__ import annotations

import html
import math
import time
import uuid
from typing import Any

from app import app
from handlers.registry import register, register_callback
from database.query import fetchrow
from database.squads_repo import get_team_squad
from database.special_players_repo import get_special_player_by_id
from database.players_repo import get_player
from database.player_upgrades_repo import (
    get_upgrade, get_upgrade_by_name, get_upgrade_tiers, next_owned_tier,
    user_owned_upgrades, purchase_upgrade, equipped_for_player, list_equipped,
    equip_upgrade, unequip_upgrade, load_snapshot_players, persist_snapshot,
)
from buttons.upgrade_buttons import (
    shop_filters, category_upgrade_buttons, purchase_keyboard, direct_purchase_keyboard,
    equip_choices, equip_confirm, unequip_choices, unequip_confirm,
)
from utils.upgrade_prices import upgrade_price
from services.player_upgrades import UPGRADES, UPGRADE_BY_KEY, eligible_for_player, role_key

STATE_TTL = 300
STATE: dict[str, dict[str, Any]] = {}
NO_KEYBOARD = {"inline_keyboard": []}


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _state_put(payload: dict[str, Any]) -> str:
    now = time.time()
    for token, state in list(STATE.items()):
        if now - float(state.get("created", 0)) > STATE_TTL:
            STATE.pop(token, None)
    token = uuid.uuid4().hex[:24]
    payload["created"] = now
    STATE[token] = payload
    return token


def _state_take(token: str, user_id: int, *, consume: bool = False) -> dict[str, Any] | None:
    state = STATE.get(token)
    if not state or int(state.get("user_id", -1)) != int(user_id):
        return None
    if time.time() - float(state.get("created", 0)) > STATE_TTL:
        STATE.pop(token, None)
        return None
    if consume:
        STATE.pop(token, None)
    return state


def _catalog_filter(category: str) -> list[Any]:
    if category == "batting":
        return [u for u in UPGRADES if u.category in {"batting", "pressure_batting"}]
    return [u for u in UPGRADES if u.category in {"pace_bowling", "spin_bowling"}]


def _shop_text(category: str, page: int) -> tuple[str, int]:
    items = _catalog_filter(category)
    total_pages = max(1, math.ceil(len(items) / 5))
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * 5:(page + 1) * 5]
    title = "🏏 BATTING UPGRADES" if category == "batting" else "🎯 BOWLING UPGRADES"
    lines = [f"<b>╭━━〔 {title} 〕━━╮</b>", ""]
    for index, u in enumerate(chunk, start=1):
        lines.append(f"<b>{index}. {u.name} • 💎 {upgrade_price(1):,}</b>")
        lines.append(f"<blockquote expandable><b>{_esc(u.description)}</b>\n\n<b>{_esc(u.detail)}</b></blockquote>")
    lines += ["", f"<b>Page {page + 1} / {total_pages}</b>", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines), total_pages


async def _ubuy_category_text(user_id: int, category: str, page: int) -> tuple[str, int, list[dict]]:
    items = _catalog_filter(category)
    total_pages = max(1, math.ceil(len(items) / 5))
    page = max(0, min(page, total_pages - 1))
    rows = []
    lines = [f"<b>╭━━〔 {'🏏 BATTING' if category == 'batting' else '🎯 BOWLING'} UPGRADES 〕━━╮</b>", ""]
    for index, u in enumerate(items[page * 5:(page + 1) * 5], start=1):
        dbu = await get_upgrade(u.key)
        next_tier = await next_owned_tier(user_id, int(dbu["upgrade_id"]))
        tier = next_tier or 4
        price = upgrade_price(tier)
        rows.append({"upgrade_key": u.key, "name": u.name, "price": price, "tier": tier})
        status = "MAX" if next_tier is None else f"Tier {tier}"
        lines.append(f"<b>{index}. {u.name} • 💎 {price:,} • {status}</b>")
        lines.append(f"<blockquote expandable><b>{_esc(u.description)}</b>\n\n<b>{_esc(u.detail)}</b></blockquote>")
    lines += ["", f"<b>Page {page + 1} / {total_pages}</b>", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines), total_pages, rows


def _owned_catalog_for_category(owned: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    keys = {u.key for u in _catalog_filter(category)}
    highest: dict[str, dict[str, Any]] = {}
    for row in owned:
        if row["upgrade_key"] not in keys:
            continue
        current = highest.get(row["upgrade_key"])
        if current is None or int(row["tier"]) > int(current["tier"]):
            highest[row["upgrade_key"]] = row
    return list(highest.values())


def _upgrade_detail(u: Any, tier: int, *, purchase: bool = False) -> str:
    price = upgrade_price(tier)
    role_text = "Batting" if u.category in {"batting", "pressure_batting"} else "Bowling"
    return (
        f"<b>╭━━〔 ⚡ { _esc(u.name).upper() } 〕━━╮</b>\n\n"
        f"<blockquote expandable><b>💎 Price : {price:,} Rubies\n"
        f"🎯 Type  : {role_text}\n"
        f"⭐ Tier  : {tier} ({tier_strength_text(tier):g}% relative)\n"
        f"📈 Effect: {_esc(u.description)}</b>\n\n"
        f"<b>{_esc(u.detail)}</b></blockquote>\n\n"
        f"<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def tier_strength_text(tier: int) -> float:
    return {1: 5.0, 2: 7.5, 3: 10.0, 4: 15.0}.get(int(tier), 5.0)


async def _player_from_owned_squad(user_id: int, query: str) -> tuple[dict | None, list[dict]]:
    squad = await get_team_squad(user_id) or []
    q = " ".join((query or "").strip().lower().split())
    if not q:
        return None, squad
    exact = [p for p in squad if " ".join(str(p.get("name") or "").lower().split()) == q]
    if len(exact) == 1:
        return dict(exact[0]), squad
    first = q.split()[0]
    matches = [p for p in squad if str(p.get("name") or "").lower().split() and str(p.get("name") or "").lower().split()[0] == first]
    if len(matches) == 1:
        return dict(matches[0]), squad
    contains = [p for p in squad if q in " ".join(str(p.get("name") or "").lower().split())]
    if len(contains) == 1:
        return dict(contains[0]), squad
    return None, (matches or contains)


def _kind(player: dict) -> str:
    return "special" if bool(player.get("is_special")) else "global"


def _player_identity_text(player: dict) -> str:
    name = str(player.get("name") or "Player")
    if player.get("is_special") and player.get("edition"):
        return f"{name} ✨ {_esc(player.get('edition'))}"
    return name


def _eligible_owned_upgrade_rows(owned: list[dict], player: dict) -> list[dict]:
    family = "spin" if any(x in f"{player.get('bowling_hand','')} {player.get('role','')}".lower() for x in ("spin","spinner","off","leg","slow","orthodox","chinaman")) else "pace"
    role = role_key(player.get("role"))
    result = []
    for row in owned:
        u = UPGRADE_BY_KEY.get(str(row["upgrade_key"]))
        if not u or not eligible_for_player(u, role=role, bowling_style=player.get("role"), bowling_hand=player.get("bowling_hand")):
            continue
        # Do not show more than one row for a key; highest owned tier is the useful loadout.
        if any(x["upgrade_key"] == row["upgrade_key"] for x in result):
            continue
        row = dict(row)
        row["display_label"] = f"{u.name} • Tier {int(row['tier'])}"
        result.append(row)
    return result


def _loadout_context(state: dict[str, Any]) -> dict[str, Any]:
    return state


@register("ushop")
async def ushop_command(message):
    user_id = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    text, total_pages = _shop_text("batting", 0)
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=shop_filters("batting", 0, total_pages, user_id))


@register_callback("upgrade_filter")
async def on_upgrade_filter(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4 or parts[1] not in {"batting", "bowling"} or not parts[2].isdigit() or not parts[3].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid upgrade filter.", show_alert=True)
        return
    category, page, owner_id = parts[1], int(parts[2]), int(parts[3])
    if owner_id != uid:
        await app.answer_callback_query(callback_query["id"], "This upgrade menu belongs to another user.", show_alert=True)
        return
    text, total_pages = _shop_text(category, page)
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=shop_filters(category, page, total_pages, uid))
    await app.answer_callback_query(callback_query["id"])


@register_callback("upgrade_page")
async def on_upgrade_page(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4 or parts[1] not in {"batting", "bowling"} or not parts[2].isdigit() or not parts[3].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid upgrade page.", show_alert=True)
        return
    category, page, owner_id = parts[1], int(parts[2]), int(parts[3])
    if owner_id != uid:
        await app.answer_callback_query(callback_query["id"], "This upgrade menu belongs to another user.", show_alert=True)
        return
    text, total_pages = _shop_text(category, page)
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=shop_filters(category, page, total_pages, uid))
    await app.answer_callback_query(callback_query["id"])


@register("ubuy")
async def ubuy_command(message):
    uid = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    raw = str(message.get("text") or "")
    arg = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) == 2 else ""
    if not arg:
        await app.send_message(chat_id, "<b>⚠️ Use /ubuy with an upgrade name.</b>", parse_mode="HTML")
        return
    category = "batting" if arg.lower() == "batting" else ("bowling" if arg.lower() == "bowling" else None)
    if category:
        page = 0
        text, total_pages, rows = await _ubuy_category_text(uid, category, page)
        token = _state_put({"kind": "shop_list", "user_id": uid, "category": category, "page": page})
        kb = category_upgrade_buttons(rows, category, page, total_pages, token, uid)
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        return
    u = await get_upgrade_by_name(arg)
    if not u:
        await app.send_message(chat_id, "<b>⚠️ Upgrade not found. Use /ushop to view available upgrades.</b>", parse_mode="HTML")
        return
    next_tier = await next_owned_tier(uid, int(u["upgrade_id"])) or 0
    if next_tier >= 4 and await next_owned_tier(uid, int(u["upgrade_id"])) is None:
        await app.send_message(chat_id, "<b>✅ This upgrade is already at Tier IV.</b>", parse_mode="HTML")
        return
    tier = next_tier or 1
    definition = UPGRADE_BY_KEY.get(str(u["upgrade_key"]))
    token = _state_put({"kind": "buy", "user_id": uid, "upgrade_key": u["upgrade_key"], "upgrade_id": int(u["upgrade_id"]), "tier": tier, "source": "direct"})
    await app.send_message(chat_id, _upgrade_detail(definition, tier, purchase=True), parse_mode="HTML", reply_markup=direct_purchase_keyboard(token))


@register_callback("ubuy_select")
async def on_ubuy_select(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        await app.answer_callback_query(callback_query["id"], "Invalid upgrade selection.", show_alert=True)
        return
    token, key = parts[1], parts[2]
    state = _state_take(token, uid, consume=True)
    if not state or state.get("kind") != "shop_list":
        await app.answer_callback_query(callback_query["id"], "This upgrade list has expired.", show_alert=True)
        return
    try:
        tier = int(parts[3])
    except ValueError:
        await app.answer_callback_query(callback_query["id"], "Invalid upgrade tier.", show_alert=True)
        return
    u = UPGRADE_BY_KEY.get(key)
    row = await get_upgrade(key)
    if not u or not row:
        await app.answer_callback_query(callback_query["id"], "Upgrade unavailable.", show_alert=True)
        return
    token2 = _state_put({"kind": "buy", "user_id": uid, "upgrade_key": key, "upgrade_id": int(row["upgrade_id"]), "tier": tier, "return": state})
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], _upgrade_detail(u, tier, purchase=True), parse_mode="HTML", reply_markup=purchase_keyboard(token2))
    await app.answer_callback_query(callback_query["id"])


@register_callback("ubuy_back")
async def on_ubuy_back(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[1:] 
    token = token[0] if token else ""
    state = _state_take(token, uid, consume=True)
    if not state or state.get("kind") != "buy" or not state.get("return"):
        await app.answer_callback_query(callback_query["id"], "Previous shop view is unavailable.", show_alert=True)
        return
    ret = state["return"]
    category = ret["category"]
    page = int(ret.get("page", 0))
    text, total_pages, rows = await _ubuy_category_text(uid, category, page)
    new_token = _state_put({"kind":"shop_list","user_id":uid,"category":category,"page":page})
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=category_upgrade_buttons(rows, category, page, total_pages, new_token, uid))
    await app.answer_callback_query(callback_query["id"])


@register_callback("ubuy_cancel")
async def on_ubuy_cancel(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This purchase request has expired.", show_alert=True)
        return
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "<b>❌ PURCHASE CANCELLED</b>\n\n<blockquote expandable><b>No Rubies were spent and no upgrade was added.</b></blockquote>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"], "Purchase cancelled.")


@register_callback("ubuy_confirm")
async def on_ubuy_confirm(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This purchase request has expired.", show_alert=True)
        return
    key = state.get("upgrade_key")
    tier = int(state.get("tier") or 1)
    row = await get_upgrade(key)
    if not row:
        await app.answer_callback_query(callback_query["id"], "This upgrade is unavailable.", show_alert=True)
        return
    result = await purchase_upgrade(uid, int(row["upgrade_id"]), tier, upgrade_price(tier))
    if result == "success":
        text = f"<b>✅ UPGRADE PURCHASED</b>\n\n<blockquote expandable><b>{_esc(row['name'])} • Tier {tier}\n💎 -{upgrade_price(tier):,} Rubies</b></blockquote>"
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "Upgrade purchased successfully.")
    elif result == "insufficient":
        await app.answer_callback_query(callback_query["id"], f"You need {upgrade_price(tier):,} Rubies.", show_alert=True)
    elif result == "already_owned":
        await app.answer_callback_query(callback_query["id"], "That tier is already owned.", show_alert=True)
    else:
        await app.answer_callback_query(callback_query["id"], "Purchase failed. No Rubies were deducted.", show_alert=True)


@register("equip")
async def equip_command(message):
    uid = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    raw = str(message.get("text") or "")
    query = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) == 2 else ""
    if not query:
        await app.send_message(chat_id, "<b>⚠️ Use /equip with a player name.</b>", parse_mode="HTML")
        return
    player, matches = await _player_from_owned_squad(uid, query)
    if player is None:
        if len(matches) > 1:
            names = "\n".join(f"<b>• {_esc(p.get('name'))}{' ✨ ' + _esc(p.get('edition')) if p.get('is_special') and p.get('edition') else ''}</b>" for p in matches[:10])
            await app.send_message(chat_id, f"<b>⚠️ Multiple players match:</b>\n\n{names}\n\n<b>Please use the full player name.</b>", parse_mode="HTML")
        else:
            await app.send_message(chat_id, "<b>⚠️ That player is not in your squad.</b>", parse_mode="HTML")
        return
    owned = await user_owned_upgrades(uid)
    eligible = _eligible_owned_upgrade_rows(owned, player)
    current = await equipped_for_player(uid, int(player.get("player_id") or 0), _kind(player))
    role = role_key(player.get("role"))
    filtered = []
    for row in eligible:
        u = UPGRADE_BY_KEY.get(str(row["upgrade_key"]))
        upgrade_slot = "bowling" if u and u.category in {"pace_bowling", "spin_bowling"} else "batting"
        occupied = bool(current and current.get("bowling_upgrade_id" if upgrade_slot == "bowling" else "batting_upgrade_id") is not None)
        if not occupied:
            filtered.append(row)
    eligible = filtered
    if not eligible:
        if current and ((role in {"batsman","wicketkeeper"} and current.get("batting_upgrade_id") is not None) or
                        (role == "bowler" and current.get("bowling_upgrade_id") is not None) or
                        (role == "allrounder" and current.get("batting_upgrade_id") is not None and current.get("bowling_upgrade_id") is not None)):
            await app.send_message(chat_id, f"<b>⚠️ {_esc(player.get('name'))} already has the eligible upgrade slot occupied. Unequip an upgrade first.</b>", parse_mode="HTML")
        else:
            await app.send_message(chat_id, f"<b>⚠️ No eligible upgrades are owned for {_esc(player.get('name'))}.</b>", parse_mode="HTML")
        return
    token = _state_put({"kind":"equip_list","user_id":uid,"player":player,"items":eligible})
    text = f"<b>╭━━〔 ⚡ EQUIP UPGRADE 〕━━╮</b>\n\n<b>Which upgrade do you want to equip on {_esc(player.get('name'))}?</b>\n\n<b>Select an owned upgrade:</b>\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=equip_choices(eligible, token))


@register_callback("equip_select")
async def on_equip_select(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        await app.answer_callback_query(callback_query["id"], "Invalid equip selection.", show_alert=True)
        return
    token, key = parts[1], parts[2]
    state = _state_take(token, uid, consume=True)
    if not state or state.get("kind") != "equip_list":
        await app.answer_callback_query(callback_query["id"], "This equip request has expired.", show_alert=True)
        return
    item = next((x for x in state["items"] if x["upgrade_key"] == key and int(x["tier"]) == int(parts[3])), None)
    if not item:
        await app.answer_callback_query(callback_query["id"], "Upgrade is no longer available.", show_alert=True)
        return
    u = UPGRADE_BY_KEY[key]
    token2 = _state_put({"kind":"equip","user_id":uid,"player":state["player"],"upgrade_key":key,"tier":int(item["tier"]),"upgrade_id":int(item["upgrade_id"]),"slot":"bowling" if u.category in {"pace_bowling","spin_bowling"} else "batting"})
    text = f"<b>Are you sure you want to equip { _esc(u.name) } on {_esc(state['player'].get('name'))}?</b>\n\n<blockquote expandable><b>{_esc(u.description)}\n\n{_esc(u.detail)}</b></blockquote>"
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=equip_confirm(token2))
    await app.answer_callback_query(callback_query["id"])


@register_callback("equip_confirm")
async def on_equip_confirm(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This equip request has expired.", show_alert=True)
        return
    player = state["player"]
    result = await equip_upgrade(uid, int(player.get("player_id") or 0), _kind(player), int(state["upgrade_id"]), int(state["tier"]), str(state["slot"]))
    if result == "success":
        u = UPGRADE_BY_KEY[state["upgrade_key"]]
        text = f"<b>✅ UPGRADE EQUIPPED</b>\n\n<blockquote expandable><b>🏏 {_esc(player.get('name'))}\n⚡ {_esc(u.name)} • Tier {int(state['tier'])}\n📈 {_esc(u.description)}\n\n{_esc(u.detail)}</b></blockquote>\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "Upgrade equipped.")
    elif result == "slot_occupied":
        await app.answer_callback_query(callback_query["id"], "That upgrade slot is already occupied. Unequip it first.", show_alert=True)
    else:
        await app.answer_callback_query(callback_query["id"], "Unable to equip this upgrade.", show_alert=True)


@register_callback("equip_cancel")
async def on_equip_cancel(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This request has expired.", show_alert=True)
        return
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "<b>❌ EQUIP CANCELLED</b>\n\n<blockquote expandable><b>No upgrade was equipped.</b></blockquote>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"], "Equip cancelled.")


@register("equiplist")
async def equiplist_command(message):
    uid = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    rows = await list_equipped(uid)
    if not rows:
        await app.send_message(chat_id, "<b>╭━━〔 ⚡ EQUIPPED UPGRADES 〕━━╮</b>\n\n<blockquote expandable><b>No players have equipped upgrades yet.</b></blockquote>\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>", parse_mode="HTML")
        return
    squad = await get_team_squad(uid) or []
    names = { (int(p.get("player_id") or 0), _kind(p)): _player_identity_text(p) for p in squad }
    lines = ["<b>╭━━〔 ⚡ EQUIPPED UPGRADES 〕━━╮</b>", ""]
    for row in rows:
        name = names.get((int(row["player_id"]), row["player_kind"]), "Player")
        upgrades = []
        if row.get("batting_name"):
            upgrades.append(f"🏏 {row['batting_name']}")
        if row.get("bowling_name"):
            upgrades.append(f"🎯 {row['bowling_name']}")
        lines.append(f"<b>👤 {name}</b>")
        for u in upgrades:
            lines.append(f"<b>⚡ {u}</b>")
        lines.append("")
    lines.append("<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


@register("unequip")
async def unequip_command(message):
    uid = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    raw = str(message.get("text") or "")
    query = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) == 2 else ""
    if not query:
        await app.send_message(chat_id, "<b>⚠️ Use /unequip with a player name.</b>", parse_mode="HTML")
        return
    player, matches = await _player_from_owned_squad(uid, query)
    if player is None:
        await app.send_message(chat_id, "<b>⚠️ That player is not in your squad.</b>", parse_mode="HTML")
        return
    current = await equipped_for_player(uid, int(player.get("player_id") or 0), _kind(player))
    if not current or (current.get("batting_upgrade_id") is None and current.get("bowling_upgrade_id") is None):
        await app.send_message(chat_id, f"<b>⚠️ {_esc(player.get('name'))} has no equipped upgrade.</b>", parse_mode="HTML")
        return
    items = []
    if current.get("batting_name"):
        items.append({"slot":"batting","display_label":f"🏏 {current['batting_name']}"})
    if current.get("bowling_name"):
        items.append({"slot":"bowling","display_label":f"🎯 {current['bowling_name']}"})
    token = _state_put({"kind":"unequip_list","user_id":uid,"player":player,"items":items})
    if len(items) == 1:
        token2 = _state_put({"kind":"unequip","user_id":uid,"player":player,"slot":items[0]["slot"],"upgrade_name":items[0]["display_label"]})
        text = f"<b>Are you sure you want to unequip the upgrade from {_esc(player.get('name'))}?</b>\n\n<blockquote expandable><b>{items[0]['display_label']}\n\n⚠️ This removes the active upgrade only. Your ownership remains.</b></blockquote>"
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=unequip_confirm(token2))
    else:
        text = f"<b>Which upgrade do you want to unequip from {_esc(player.get('name'))}?</b>\n\n<b>Select the equipped upgrade:</b>"
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=unequip_choices(items, token))


@register_callback("unequip_select")
async def on_unequip_select(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Invalid unequip selection.", show_alert=True)
        return
    state = _state_take(parts[1], uid, consume=True)
    if not state or state.get("kind") != "unequip_list":
        await app.answer_callback_query(callback_query["id"], "This request has expired.", show_alert=True)
        return
    slot = parts[2]
    item = next((x for x in state["items"] if x["slot"] == slot), None)
    if not item:
        await app.answer_callback_query(callback_query["id"], "Upgrade is no longer equipped.", show_alert=True)
        return
    token = _state_put({"kind":"unequip","user_id":uid,"player":state["player"],"slot":slot,"upgrade_name":item["display_label"]})
    text = f"<b>Are you sure you want to unequip the upgrade from {_esc(state['player'].get('name'))}?</b>\n\n<blockquote expandable><b>{item['display_label']}\n\n⚠️ The upgrade will be removed from the active loadout, but your ownership stays.</b></blockquote>"
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=unequip_confirm(token))
    await app.answer_callback_query(callback_query["id"])


@register_callback("unequip_confirm")
async def on_unequip_confirm(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This request has expired.", show_alert=True)
        return
    player = state["player"]
    result = await unequip_upgrade(uid, int(player.get("player_id") or 0), _kind(player), state["slot"])
    if result == "success":
        text = f"<b>✅ UPGRADE UNEQUIPPED</b>\n\n<blockquote expandable><b>🏏 {_esc(player.get('name'))}\n⚡ {state['upgrade_name']}\n\nThe upgrade is no longer active. Your ownership is unchanged.</b></blockquote>\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "Upgrade unequipped.")
    else:
        await app.answer_callback_query(callback_query["id"], "This upgrade is already unequipped.", show_alert=True)


@register_callback("unequip_cancel")
async def on_unequip_cancel(callback_query):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    token = str(callback_query.get("data") or "").split(":")[-1]
    state = _state_take(token, uid, consume=True)
    if not state:
        await app.answer_callback_query(callback_query["id"], "This request has expired.", show_alert=True)
        return
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "<b>❌ UNEQUIP CANCELLED</b>\n\n<blockquote expandable><b>The active loadout was not changed.</b></blockquote>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"], "Unequip cancelled.")
