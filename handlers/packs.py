from __future__ import annotations

import asyncio
import html
import json

from handlers.registry import register, register_callback
from app import app
from database.query import execute, fetchrow
from services.card_provider import get_player_card_bytes
from services.pack_system import (PACK_CATALOG, create_purchase_request, confirm_purchase, inventory, normalize_pack_key, open_pack, get_opening,
                                  pack_shop_text, pending_cards, squad_choices, release_card, replace_card, MAX_SQUAD_SIZE)
from services.quest_engine import record_quest_event
from utils.PremiumEmoji import coins_emoji_html, pack_emoji_html, rubies_emoji_html, sigil_emoji_html, PACK_IMAGE_FILE_IDS
from utils.country_flags import flag_for


def _button(text: str, data: str, style: str):
    return {"text": text, "callback_data": data, "style": style}


async def _edit_ui_message(callback_query: dict, text: str, reply_markup=None):
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    if not chat_id or not message_id:
        return
    if message.get("photo"):
        await app.edit_message_caption(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        await app.edit_message_text(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


def _buy_keyboard(request_id: str, user_id: int):
    return {"inline_keyboard": [[
        _button("YES", f"pack_confirm:{request_id}:{user_id}", "success"),
        _button("CONFIRM", f"pack_confirm:{request_id}:{user_id}", "success"),
        _button("CANCEL", f"pack_cancel:{request_id}:{user_id}", "danger"),
    ]]}


def _inventory_keyboard(inv: dict[str, int], user_id: int):
    rows = []
    for key, p in PACK_CATALOG.items():
        qty = int(inv.get(key) or 0)
        if qty > 0:
            rows.append([_button(f"{p['name']} × {qty}", f"pack_open:{key}:{user_id}", "danger")])
    return {"inline_keyboard": rows}


def _pagination_keyboard(opening_id: str, user_id: int, page: int, total: int, card: dict | None = None):
    prev_style = "danger"
    next_style = "success"
    page_style = "primary"
    rows = []
    if card and card.get("status") == "pending" and card.get("card_id"):
        cid = int(card["card_id"])
        decision = [_button(f"♻️ Release (+{int(card.get('sell_value') or 0):,})", f"packdup:rel:{cid}:{user_id}", "success")]
        if card.get("can_replace"):
            decision.append(_button("🔁 Replace", f"packdup:rep:{cid}:{user_id}", "primary"))
        rows.append(decision)
    rows.append([
        _button("PREVIOUS", f"pack_page:{opening_id}:{user_id}:{max(0,page-1)}", prev_style),
        _button(f"{page+1}/{total}", f"pack_page_noop:{opening_id}:{user_id}:{page}", page_style),
        _button("NEXT", f"pack_page:{opening_id}:{user_id}:{min(total-1,page+1)}", next_style),
    ])
    return {"inline_keyboard": rows}


def _purchase_text(pack_key: str, balance: int, post_balance: int | None = None) -> str:
    p = PACK_CATALOG[pack_key]
    sg = sigil_emoji_html()
    coins = coins_emoji_html()
    rubies = rubies_emoji_html()
    post = balance - int(p["price"]) if post_balance is None else post_balance
    return (
        f"<b>╭━━━〔 📦 PACK PURCHASE 〕━━━╮</b>\n\n"
        f"{pack_emoji_html(pack_key)} <b>{html.escape(p['name'])}</b>\n\n"
        f"{sg} <b>{p['price']:,} SG</b>\n"
        f"├ Current Balance : <b>{balance:,} SG</b>\n"
        f"╰ After Purchase  : <b>{post:,} SG</b>\n\n"
        f"🎴 <b>{p['cards']} Cards</b>\n"
        f"├ Core : <b>{p['core'][0]}-{p['core'][1]} OVR</b>\n"
        f"└ Bonus : <b>{p['bonus'][0]}-{p['bonus'][1]} OVR</b>\n\n"
        f"🎁 {coins} <b>+{p['coins']:,} Coins</b> • {rubies} <b>+{p['rubies']:,} Rubies</b>\n\n"
        f"<blockquote><i>{html.escape(p['description'])}</i></blockquote>\n\n"
        "<b>Confirm this purchase?</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _delivery_line(card: dict) -> str:
    status = card.get("status")
    if status == "added":
        return "✅ <b>Added to your squad</b>"
    if status == "released":
        return f"♻️ <b>Released</b> · {coins_emoji_html()} <b>+{int(card.get('resolution_coins') or 0):,} Coins</b>"
    if status == "replaced":
        return f"🔁 <b>Replaced a squad player</b> · {coins_emoji_html()} <b>+{int(card.get('resolution_coins') or 0):,} Coins</b> from the sale"
    if status == "pending":
        worth = f"{coins_emoji_html()} <b>{int(card.get('sell_value') or 0):,} Coins</b>"
        if card.get("reason") == "duplicate":
            return f"⚠️ <b>Already in your squad.</b> Release it for {worth}."
        return f"⚠️ <b>Your squad is full ({MAX_SQUAD_SIZE}/{MAX_SQUAD_SIZE}).</b> Release it for {worth} or Replace a squad player."
    return ""


def _card_text(pack_key: str, cards: list[dict], page: int, coins: int, rubies: int) -> str:
    p = PACK_CATALOG[pack_key]
    card = cards[page]
    player = card["player"]
    name = html.escape(str(player.get("name") or "Player"))
    if player.get("is_special") and player.get("edition"):
        name += f" ✨ {html.escape(str(player.get('edition')))}"
    role = html.escape(str(player.get("role") or "Player"))
    country = html.escape(str(player.get("country") or "Unknown"))
    ovr = int(card.get("ovr") or 0)
    rarity = html.escape(str(card.get("rarity") or "Unknown"))
    bat = int(player.get("bat_level") or 0)
    bowl = int(player.get("bowl_level") or 0)
    kind = "CORE" if card.get("kind") == "core" else "BONUS"
    return (
        f"<b>╭━━━〔 {pack_emoji_html(pack_key)} {html.escape(p['name']).upper()} 〕━━━╮</b>\n\n"
        f"🎴 <b>CARD {page+1}/{len(cards)}</b> • <b>{kind}</b>\n\n"
        f"👤 <b>{name}</b> {flag_for(player.get('country'))}\n"
        f"⚡ <b>OVR:</b> {ovr} • 🏅 <b>{rarity}</b>\n"
        f"🏏 <b>{role}</b> • 🌍 <b>{country}</b>\n"
        f"├ BAT : <b>{bat}</b>\n"
        f"╰ BOWL: <b>{bowl}</b>\n\n"
        f"{coins_emoji_html()} <b>+{int(coins):,} Coins</b>\n"
        f"{rubies_emoji_html()} <b>+{int(rubies):,} Rubies</b>\n\n"
        f"{_delivery_line(card)}\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _edit_final_result(callback_query: dict, opening: dict, page: int):
    cards = opening.get("cards") or []
    if not cards:
        return
    page = max(0, min(page, len(cards) - 1))
    card = cards[page]
    text = _card_text(opening["pack_key"], cards, page, int(opening.get("coins") or 0), int(opening.get("rubies") or 0))
    player = dict(card.get("player") or {})
    image_bytes, _ = await get_player_card_bytes(player)
    await app.edit_message_media(
        callback_query["message"]["chat"]["id"],
        callback_query["message"]["message_id"],
        image_bytes,
        caption=text,
        parse_mode="HTML",
        reply_markup=_pagination_keyboard(opening["opening_id"], int(opening["user_id"]), page, len(cards), card),
    )


@register("packs")
async def packs_command(message):
    chat_id = int(message["chat"]["id"])
    await app.send_message(chat_id, pack_shop_text(), parse_mode="HTML")


@register("buypack")
async def buypack_command(message):
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    parts = str(message.get("text") or "").split(maxsplit=1)
    key = normalize_pack_key(parts[1] if len(parts) == 2 else None)
    if not key:
        await app.send_message(chat_id, "<b>⚠️ Use /buypack bronze|silver|gold|diamond|platinum</b>", parse_mode="HTML")
        return
    row = await fetchrow("SELECT sigils FROM users WHERE user_id=$1;", user_id)
    balance = int(row["sigils"] or 0) if row else 0
    if balance < int(PACK_CATALOG[key]["price"]):
        await app.send_message(chat_id, f"<b>❌ Not enough SG.</b>\n\nYou need <b>{PACK_CATALOG[key]['price']:,} SG</b> but have <b>{balance:,} SG</b>.", parse_mode="HTML")
        return
    req = await create_purchase_request(user_id, key)
    if not req:
        await app.send_message(chat_id, "<b>⚠️ Could not create the purchase request.</b>", parse_mode="HTML")
        return
    image_id = PACK_IMAGE_FILE_IDS.get(key)
    text = _purchase_text(key, balance)
    if image_id:
        try:
            await app.send_photo(chat_id, photo=image_id, caption=text, parse_mode="HTML", reply_markup=_buy_keyboard(req["request_id"], user_id))
            return
        except Exception as exc:
            print(f"[packs] pack image unavailable: {exc!r}")
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=_buy_keyboard(req["request_id"], user_id))


@register_callback("pack_confirm")
async def pack_confirm_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Invalid purchase request.", show_alert=True)
        return
    _, request_id, owner_id = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This purchase request is not yours.", show_alert=True)
        return
    result = await confirm_purchase(uid, request_id)
    if result.get("status") == "success":
        key = result["pack_key"]
        text = (
            f"<b>✅ PACK PURCHASED</b>\n\n{pack_emoji_html(key)} <b>{html.escape(PACK_CATALOG[key]['name'])}</b>\n"
            f"{sigil_emoji_html()} <b>-{int(result['price']):,} SG</b>\n"
            f"{sigil_emoji_html()} <b>Balance: {int(result['balance']):,} SG</b>\n\n"
            "<blockquote><i>The Pack has been added to your unopened inventory. Use /openpack to reveal it.</i></blockquote>"
        )
        await _edit_ui_message(callback_query, text, {"inline_keyboard": []})
        await app.answer_callback_query(callback_query["id"], "Pack purchased!")
        try:
            await record_quest_event(uid, "PACK_PURCHASED", metadata={"pack_key": key})
        except Exception:
            pass
    elif result.get("status") == "insufficient":
        await app.answer_callback_query(callback_query["id"], "Not enough SG.", show_alert=True)
    elif result.get("status") == "owner":
        await app.answer_callback_query(callback_query["id"], "This purchase request is not yours.", show_alert=True)
    else:
        await app.answer_callback_query(callback_query["id"], "This purchase request has already been processed.", show_alert=True)


@register_callback("pack_cancel")
async def pack_cancel_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Invalid purchase request.", show_alert=True)
        return
    _, request_id, owner_id = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This purchase request is not yours.", show_alert=True)
        return
    try:
        rid = __import__("uuid").UUID(request_id)
        await execute("UPDATE pack_purchase_requests SET status='cancelled',processed_at=NOW() WHERE request_id=$1 AND user_id=$2 AND status='pending';", rid, uid)
    except Exception:
        pass
    await _edit_ui_message(callback_query, "<b>❎ Pack purchase cancelled.</b>", {"inline_keyboard": []})
    await app.answer_callback_query(callback_query["id"], "Cancelled")


@register("mypacks")
async def mypacks_command(message):
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    inv = await inventory(user_id)
    lines = ["<b>╭━━━〔 📦 MY PACKS 〕━━━╮</b>", ""]
    total = 0
    for key, p in PACK_CATALOG.items():
        qty = int(inv[key])
        total += qty
        lines.append(f"{pack_emoji_html(key)} <b>{p['name']}</b> × <b>{qty}</b>")
    lines += ["", f"<b>Total Unopened Packs: {total}</b>", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


@register("openpack")
async def openpack_command(message):
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    parts = str(message.get("text") or "").split(maxsplit=1)
    key = normalize_pack_key(parts[1] if len(parts) == 2 else None)
    if not key:
        inv = await inventory(user_id)
        kb = _inventory_keyboard(inv, user_id)
        if not kb["inline_keyboard"]:
            await app.send_message(chat_id, "<b>📦 You don’t have any unopened Packs.</b>", parse_mode="HTML")
            return
        await app.send_message(chat_id, "<b>╭━━━〔 📦 OPEN PACK 〕━━━╮</b>\n\n<b>Choose an unopened Pack</b>", parse_mode="HTML", reply_markup=kb)
        return
    await _start_pack_opening(chat_id, user_id, key)


async def _start_pack_opening(chat_id: int, user_id: int, key: str):
                                                                             
                                                                               
    result = await open_pack(user_id, key)
    if result.get("status") == "empty":
        await app.send_message(chat_id, "<b>❌ You do not have this unopened Pack.</b>", parse_mode="HTML")
        return
    if result.get("status") == "no_player":
        await app.send_message(chat_id, "<b>⚠️ This Pack cannot be opened right now because an eligible player pool is unavailable. Your Pack was not consumed.</b>", parse_mode="HTML")
        return
    if result.get("status") != "success":
        await app.send_message(chat_id, "<b>⚠️ Pack opening failed safely. Your Pack was not consumed.</b>", parse_mode="HTML")
        return
    opening_id = result["opening_id"]
    image_id = PACK_IMAGE_FILE_IDS.get(key)
    state_texts = [
        f"{pack_emoji_html(key)} <b>Opening {PACK_CATALOG[key]['name']}...</b>",
        "🔍 <b>Finding guaranteed core player...</b>",
        "🎴 <b>Finding bonus player...</b>",
        "✨ <b>Finalizing rewards...</b>",
    ]
    progress = None
    for idx, stage in enumerate(state_texts):
        try:
            if progress is None:
                if image_id:
                    try:
                        progress = await app.send_photo(chat_id, photo=image_id, caption=stage, parse_mode="HTML")
                    except Exception:
                        progress = await app.send_message(chat_id, stage, parse_mode="HTML")
                else:
                    progress = await app.send_message(chat_id, stage, parse_mode="HTML")
            else:
                try:
                    if progress.get("photo"):
                        await app.edit_message_caption(chat_id, progress["message_id"], stage, parse_mode="HTML")
                    else:
                        await app.edit_message_text(chat_id, progress["message_id"], stage, parse_mode="HTML")
                except Exception:
                    pass
        finally:
            await asyncio.sleep(0.8 if idx < 3 else 0.5)
    if progress:
        try:
            await app.delete_message(chat_id, progress["message_id"])
        except Exception:
            pass
    opening = await get_opening(user_id, opening_id)
    if not opening:
        await app.send_message(chat_id, "<b>⚠️ Opening result could not be loaded.</b>", parse_mode="HTML")
        return
    cards = opening.get("cards") or []
    if not cards:
        await app.send_message(chat_id, "<b>⚠️ Opening result is empty.</b>", parse_mode="HTML")
        return
    card = cards[0]
    text = _card_text(key, cards, 0, int(opening.get("coins") or 0), int(opening.get("rubies") or 0))
    image_bytes, _ = await get_player_card_bytes(dict(card.get("player") or {}))
    await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=_pagination_keyboard(opening_id, user_id, 0, len(cards), card))
    try:
        await record_quest_event(user_id, "PACK_OPENED", metadata={"pack_key": key, "opening_id": opening_id})
    except Exception:
        pass


@register_callback("pack_open")
async def pack_open_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        await app.answer_callback_query(callback_query["id"], "Invalid Pack selection.", show_alert=True)
        return
    _, key, owner_id = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This Pack menu is not yours.", show_alert=True)
        return
    await app.answer_callback_query(callback_query["id"], "Opening Pack...")
    try:
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "📦 <b>Preparing your Pack opening...</b>", parse_mode="HTML", reply_markup={"inline_keyboard": []})
    except Exception:
        pass
    await _start_pack_opening(callback_query["message"]["chat"]["id"], uid, key)


@register_callback("pack_page")
async def pack_page_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid page.", show_alert=True)
        return
    _, opening_id, owner_id, page_s = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This Pack result is not yours.", show_alert=True)
        return
    opening = await get_opening(uid, opening_id)
    if not opening:
        await app.answer_callback_query(callback_query["id"], "Pack result expired or unavailable.", show_alert=True)
        return
    total = len(opening.get("cards") or [])
    page = max(0, min(int(page_s), max(0,total-1)))
    try:
        await _edit_final_result(callback_query, opening, page)
    except Exception as exc:
        print(f"[packs] page update failed: {exc!r}")
        await app.answer_callback_query(callback_query["id"], "Could not update this page.", show_alert=True)
        return
    await app.answer_callback_query(callback_query["id"], f"Card {page+1}/{total}")


@register_callback("pack_page_noop")
async def pack_page_noop_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        await app.answer_callback_query(callback_query["id"], "Invalid page.", show_alert=True)
        return
    _, opening_id, owner_id, page_s = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This Pack result is not yours.", show_alert=True)
        return
    opening = await get_opening(uid, opening_id)
    if not opening:
        await app.answer_callback_query(callback_query["id"], "Pack result unavailable.", show_alert=True)
        return
    total = len(opening.get("cards") or [])
    page = max(0, min(int(page_s), max(0,total-1)))
    await app.answer_callback_query(callback_query["id"], f"Card {page+1}/{total}")


# ---------------------------------------------------------------------------------------------------------------------
# Release & Replace for pack cards that cannot join the squad (already owned / squad full)
# ---------------------------------------------------------------------------------------------------------------------
def _short(name: object, n: int = 15) -> str:
    text = str(name or "Player")
    return text if len(text) <= n else text[: n - 1] + "…"


async def _show_opening_page(callback_query: dict, user_id: int, opening_id: str, position: int) -> None:
    opening = await get_opening(user_id, opening_id)
    if not opening or not opening.get("cards"):
        await _edit_ui_message(callback_query, "<b>✅ Done.</b>", {"inline_keyboard": []})
        return
    page = max(0, min(int(position) - 1, len(opening["cards"]) - 1))
    try:
        await _edit_final_result(callback_query, opening, page)
    except Exception as exc:
        print(f"[packs] could not redraw the pack card: {exc!r}")
        card = opening["cards"][page]
        await _edit_ui_message(callback_query, _card_text(opening["pack_key"], opening["cards"], page, int(opening.get("coins") or 0), int(opening.get("rubies") or 0)),
                               _pagination_keyboard(opening["opening_id"], user_id, page, len(opening["cards"]), card))


async def _find_pending(user_id: int, card_id: int) -> dict | None:
    return next((c for c in await pending_cards(user_id) if int(c["card_id"]) == int(card_id)), None)


@register_callback("packdup")
async def pack_duplicate_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if len(parts) < 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid choice.", show_alert=True)
        return
    action, card_id, owner_id = parts[1], int(parts[2]), int(parts[3])
    if uid != owner_id:
        await app.answer_callback_query(callback_query["id"], "This pack card is not yours.", show_alert=True)
        return

    if action in {"rel", "no"}:
        res = await release_card(uid, card_id)
        if res.get("status") == "success":
            await app.answer_callback_query(callback_query["id"], f"Released · +{int(res['coins']):,} coins")
            await _show_opening_page(callback_query, uid, res["opening_id"], res["position"])
        elif res.get("status") == "already_resolved":
            await app.answer_callback_query(callback_query["id"], "This card was already handled.", show_alert=True)
            await _show_opening_page(callback_query, uid, res["opening_id"], res["position"])
        else:
            await app.answer_callback_query(callback_query["id"], "Could not release this card.", show_alert=True)
        return

    card = await _find_pending(uid, card_id)
    if card is None:
        await app.answer_callback_query(callback_query["id"], "This card was already handled.", show_alert=True)
        return
    player = card["player"]
    name = html.escape(str(player.get("name") or "Player"))
    if action == "rep":
        if not card.get("can_replace"):
            await app.answer_callback_query(callback_query["id"], "This player is already in your squad - a squad cannot hold the same card twice. Release it for coins.", show_alert=True)
            return
        squad = await squad_choices(uid)
        rows, row = [], []
        for p in squad:
            row.append(_button(f"{_short(p['name'])} · {p['ovr']}", f"packdup:pick:{card_id}:{uid}:{p['player_id']}", "primary"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows.append([_button("⬅ Back", f"packdup:back:{card_id}:{uid}", "danger"), _button(f"♻️ Release (+{card['sell_value']:,})", f"packdup:rel:{card_id}:{uid}", "success")])
        text = (f"<b>╭━━━〔 🔁 REPLACE PLAYER 〕━━━╮</b>\n\n"
                f"🎴 <b>{name}</b> • ⚡ <b>{card['ovr']} OVR</b>\n\n"
                f"<b>Choose the squad player to replace.</b>\nThe chosen player is sold for coins and {name} takes the place.\n\n"
                "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
        await _edit_ui_message(callback_query, text, {"inline_keyboard": rows})
        await app.answer_callback_query(callback_query["id"])
        return
    if action == "back":
        await _show_opening_page(callback_query, uid, card["opening_id"], card["position"])
        await app.answer_callback_query(callback_query["id"])
        return
    if action in {"pick", "yes"}:
        if len(parts) != 5 or not parts[4].lstrip("-").isdigit():
            await app.answer_callback_query(callback_query["id"], "Invalid choice.", show_alert=True)
            return
        target_id = int(parts[4])
        target = next((p for p in await squad_choices(uid) if p["player_id"] == target_id), None)
        if target is None:
            await app.answer_callback_query(callback_query["id"], "That player is no longer in your squad.", show_alert=True)
            return
        if action == "pick":
            text = (f"<b>╭━━━〔 🔁 CONFIRM REPLACE 〕━━━╮</b>\n\n"
                    f"🔴 <b>Out:</b> {html.escape(target['name'])} • {target['ovr']} OVR\n"
                    f"   {coins_emoji_html()} Sold for <b>{target['sell_value']:,} Coins</b>\n\n"
                    f"🟢 <b>In:</b> {name} • {card['ovr']} OVR\n\n"
                    f"<b>Replace {html.escape(target['name'])} with {name}?</b>\n"
                    "<i>Cancel releases the new player for coins instead.</i>\n\n"
                    "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
            kb = {"inline_keyboard": [[
                _button("✅ Yes, Replace", f"packdup:yes:{card_id}:{uid}:{target_id}", "success"),
                _button("❌ Cancel", f"packdup:no:{card_id}:{uid}", "danger"),
            ], [_button("⬅ Back", f"packdup:rep:{card_id}:{uid}", "primary")]]}
            await _edit_ui_message(callback_query, text, kb)
            await app.answer_callback_query(callback_query["id"])
            return
        res = await replace_card(uid, card_id, target_id)
        if res.get("status") == "success":
            await app.answer_callback_query(callback_query["id"], f"Replaced · +{int(res['coins']):,} coins")
            await _show_opening_page(callback_query, uid, res["opening_id"], res["position"])
        elif res.get("status") == "already_resolved":
            await app.answer_callback_query(callback_query["id"], "This card was already handled.", show_alert=True)
        elif res.get("status") == "target_missing":
            await app.answer_callback_query(callback_query["id"], "That player is no longer in your squad.", show_alert=True)
        elif res.get("status") == "duplicate_card":
            await app.answer_callback_query(callback_query["id"], "Already in your squad - release it for coins.", show_alert=True)
        else:
            await app.answer_callback_query(callback_query["id"], "Could not replace. Nothing was changed.", show_alert=True)
        return
    await app.answer_callback_query(callback_query["id"], "Invalid choice.", show_alert=True)


@register("packcards")
async def packcards_command(message):
    """Pack cards that are still waiting for a Release / Replace decision."""
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    cards = await pending_cards(user_id)
    if not cards:
        await app.send_message(chat_id, "<b>✅ No pack cards are waiting. Every pulled player is in your squad.</b>", parse_mode="HTML")
        return
    for card in cards[:5]:
        pl = card["player"]
        text = (f"<b>╭━━━〔 {pack_emoji_html(card['pack_key']) if card['pack_key'] in PACK_CATALOG else '📦'} PACK CARD 〕━━━╮</b>\n\n"
                f"👤 <b>{html.escape(str(pl.get('name') or 'Player'))}</b> • ⚡ <b>{card['ovr']} OVR</b>\n\n"
                f"{_delivery_line({'status': 'pending', 'reason': card['reason'], 'sell_value': card['sell_value']})}\n\n"
                "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
        cid = int(card["card_id"])
        row = [_button(f"♻️ Release (+{card['sell_value']:,})", f"packdup:rel:{cid}:{user_id}", "success")]
        if card.get("can_replace"):
            row.append(_button("🔁 Replace", f"packdup:rep:{cid}:{user_id}", "primary"))
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup={"inline_keyboard": [row]})
    if len(cards) > 5:
        await app.send_message(chat_id, f"<b>{len(cards) - 5} more waiting — handle these first, then send /packcards again.</b>", parse_mode="HTML")
