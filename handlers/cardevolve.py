from __future__ import annotations

import html

from app import app
from handlers.registry import register, register_callback
from buttons.play_buttons import _styled_button
from pyrogram.types import InlineKeyboardMarkup
from database.special_players_repo import display_edition
from services.match_guard import EVOLVE_BLOCKED_TEXT, user_in_match
from services import card_evolve
from services.card_identity import card_entity_id, player_kind
from services.card_provider import get_player_card_bytes
from utils.country_flags import flag_for
from utils.debut_gate import has_completed_debut
from utils.rarity import get_rarity
from utils.style import batting_style_text, bowling_style_text

NO_KEYBOARD = {"inline_keyboard": []}
LIST_LIMIT = 8


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _version(card: dict) -> str | None:
    kind = player_kind(card)
    if kind == "special" and card.get("edition"):
        return display_edition(card.get("edition"))
    if kind == "showcase":
        return card.get("showcase_name") or "Showcase"
    return None


def _label(card: dict) -> str:
    version = _version(card)
    base = f"{card.get('name')} OVR {card_evolve.card_ovr(card)}"
    return f"{base} ({version})" if version else base


def _pick_data(user_id: int, old: dict, new: dict) -> str:
    return f"cevo_pick:{user_id}:{int(old['player_id'])}:{player_kind(new)}:{card_entity_id(new)}"


def _confirm_keyboard(user_id: int, old: dict, new: dict) -> InlineKeyboardMarkup:
    tail = f"{user_id}:{int(old['player_id'])}:{player_kind(new)}:{card_entity_id(new)}"
    return InlineKeyboardMarkup([[
        _styled_button("✅ Confirm Evolve", f"cevo_ok:{tail}", "success"),
        _styled_button("✖️ Cancel", f"cevo_no:{user_id}", "danger"),
    ]])


def _preview_text(old: dict, new: dict) -> str:
    quote = card_evolve.evolve_quote(old, new)
    flag = flag_for(new.get("country"))
    lines = [
        "<b>╭━━━〔 🔁 CARD EVOLVE 〕━━━╮</b>",
        f"<blockquote><b>👤 {_esc(new.get('name'))} {flag}</b></blockquote>",
        f"<blockquote><b>⬆️ OVR {quote['old_ovr']} ➜ {quote['new_ovr']}  •  💎 {_esc(get_rarity(quote['new_ovr']))}</b></blockquote>",
    ]
    version = _version(new)
    if version:
        lines.append(f"<blockquote><b>✨ Version ➤ {_esc(version)}</b></blockquote>")
    lines.append(
        "<blockquote><b><i>"
        f"├ ⚡ Bat Lv.     : {int(new.get('bat_level') or 0)}\n"
        f"├ 🎯 Ball Lv.    : {int(new.get('bowl_level') or 0)}\n"
        f"├ 🏏 Batting     : {_esc(batting_style_text(new.get('batting_hand')))}\n"
        f"├ 🥎 Bowling     : {_esc(bowling_style_text(new.get('bowling_hand')))}\n"
        f"├ 🛒 New card    : 🪙 {quote['buy_price']:,}\n"
        f"├ ♻️ Old card    : 🪙 {quote['sell_value']:,}\n"
        f"╰ 💳 You pay     : 🪙 {quote['cost']:,}"
        "</i></b></blockquote>"
    )
    lines += [
        "<b>════════════════════</b>",
        "",
        "<b>Your old card is replaced. Its Playing XI spot, captaincy and equipped upgrades move to the new card.</b>",
    ]
    return "\n".join(lines)


async def _send_preview(chat_id: int, user_id: int, old: dict, new: dict) -> None:
    text = _preview_text(old, new)
    keyboard = _confirm_keyboard(user_id, old, new)
    try:
        image, _custom = await get_player_card_bytes(new)
        await app.send_photo(chat_id, photo=image, caption=text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        print(f"[cardevolve] card image failed ({exc!r}), sending text only")
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


async def _edit(callback_query: dict, text: str, keyboard=NO_KEYBOARD) -> None:
    message = callback_query.get("message") or {}
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    if message.get("photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)


def _query(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    return parts[1].strip().lower() if len(parts) > 1 and parts[1].strip() else None


async def _send_rich(chat_id: int, thread_id, rich_html: str, fallback: str, keyboard=None) -> None:
    try:
        await app.send_rich_message(chat_id, rich_html, message_thread_id=thread_id, reply_markup=keyboard)
    except Exception as exc:
        print(f"[cardevolve] rich message failed, using HTML fallback: {exc!r}")
        await app.send_message(chat_id, fallback, parse_mode="HTML", reply_markup=keyboard)


@register("evolve")
@register("cardevolve")
async def evolve_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if await user_in_match(user_id):
        await app.send_message(chat_id, f"<b>⚠️ {EVOLVE_BLOCKED_TEXT}</b>", parse_mode="HTML")
        return
    if not await has_completed_debut(user_id):
        await app.send_message(chat_id, "<b>⚠️ Complete your /debut first to unlock player collection.</b>", parse_mode="HTML")
        return

    squad, targets = await card_evolve.targets_for_user(user_id)
    wanted = _query(message.get("text", ""))
    owned = [card for card in squad if int(card.get("player_id") or 0) in targets]
    if wanted:
        owned = [card for card in owned if wanted in str(card.get("name") or "").lower()]
    thread_id = message.get("message_thread_id")
    if not owned:
        detail = (
            "None of your cards matches that name and has a higher edition to evolve to."
            if wanted else
            "None of your cards has a higher edition available right now. "
            "When a better edition of a player you own is released, it shows up here."
        )
        await _send_rich(chat_id, thread_id, f"<h2>🔁 CARD EVOLVE</h2><p>{detail}</p>", f"<b>🔁 CARD EVOLVE</b>\n\n<blockquote>{detail}</blockquote>")
        return

    if wanted and len(owned) == 1 and len(targets[int(owned[0]["player_id"])]) == 1:
        await _send_preview(chat_id, user_id, owned[0], targets[int(owned[0]["player_id"])][0])
        return

    rows = []
    blocks = [
        "<h2>🔁 CARD EVOLVE</h2>",
        "<p>Move a card to a higher edition of the same player. The old card is replaced and only the price difference is charged.</p>",
        "<hr/>",
    ]
    plain = ["<b>🔁 CARD EVOLVE</b>", "", "<blockquote><b>Pick the edition you want to move to. The old card is replaced and only the price difference is charged.</b></blockquote>", ""]
    shown = 0
    for card in sorted(owned, key=lambda item: -card_evolve.card_ovr(item)):
        for option in targets[int(card["player_id"])]:
            if shown >= LIST_LIMIT:
                break
            quote = card_evolve.evolve_quote(card, option)
            version = _version(option)
            blocks.append(
                "<details>"
                f"<summary>⬆️ <b>{_esc(card.get('name'))}</b> {quote['old_ovr']} ➜ {quote['new_ovr']} — 🪙 {quote['cost']:,}</summary>"
                + (f"<p>✨ <b>Version:</b> {_esc(version)}</p>" if version else "")
                + f"<p>⚡ <b>Bat Lv.:</b> {int(option.get('bat_level') or 0)}  🎯 <b>Ball Lv.:</b> {int(option.get('bowl_level') or 0)}</p>"
                f"<p>🛒 <b>New card:</b> 🪙 {quote['buy_price']:,}</p>"
                f"<p>♻️ <b>Your card returns:</b> 🪙 {quote['sell_value']:,}</p>"
                f"<p>💳 <b>You pay:</b> 🪙 {quote['cost']:,}</p>"
                "</details>"
            )
            plain.append(f"• <b>{_esc(card.get('name'))}</b> {quote['old_ovr']} ➜ {quote['new_ovr']} • 🪙 {quote['cost']:,}")
            rows.append([_styled_button(f"⇪ Evolve to {_label(option)}", _pick_data(user_id, card, option), "primary")])
            shown += 1
    footer = "Tap a button below to preview the card before you confirm."
    if shown >= LIST_LIMIT:
        footer += " Showing the first few; use /evolve player name for a specific card."
    blocks.append(f"<footer>{footer}</footer>")
    plain += ["", f"<i>{footer}</i>"]
    await _send_rich(chat_id, thread_id, "\n".join(blocks), "\n".join(plain), InlineKeyboardMarkup(rows))


def _parse(callback_query: dict, size: int) -> list[str] | None:
    parts = str(callback_query.get("data") or "").split(":")
    return parts[1:] if len(parts) == size + 1 else None


async def _deny(callback_query: dict, text: str) -> None:
    await app.answer_callback_query(callback_query["id"], text, show_alert=True)


@register_callback("cevo_pick")
async def on_pick(callback_query):
    parts = _parse(callback_query, 4)
    presser = int((callback_query.get("from") or {}).get("id") or 0)
    if not parts or not parts[0].lstrip("-").isdigit() or int(parts[0]) != presser:
        await _deny(callback_query, "This evolve menu belongs to another player.")
        return
    if await user_in_match(presser):
        await _deny(callback_query, EVOLVE_BLOCKED_TEXT)
        return
    _owner, old_id, kind, entity_id = parts
    squad = await card_evolve.read_squad(presser)
    old = next((card for card in squad if int(card.get("player_id") or 0) == int(old_id)), None)
    if not old:
        await _deny(callback_query, "You no longer own that card.")
        return
    new = await card_evolve.load_card(kind, int(entity_id))
    if not new or card_evolve.card_ovr(new) <= card_evolve.card_ovr(old) or str(new.get("name")).lower() != str(old.get("name")).lower():
        await _deny(callback_query, "That edition is not available for an evolution any more.")
        return
    await app.answer_callback_query(callback_query["id"])
    await _send_preview(callback_query["message"]["chat"]["id"], presser, old, new)


@register_callback("cevo_no")
async def on_cancel(callback_query):
    parts = _parse(callback_query, 1)
    presser = int((callback_query.get("from") or {}).get("id") or 0)
    if not parts or not parts[0].lstrip("-").isdigit() or int(parts[0]) != presser:
        await _deny(callback_query, "This card evolution belongs to another player.")
        return
    await _edit(callback_query, "<b>🕐 Evolution cancelled. Your squad stays exactly as it was.</b>")
    await app.answer_callback_query(callback_query["id"])


_FAILURES = {
    "user_missing": "⚠️ Start the bot first.",
    "player_not_owned": "⚠️ You no longer own the card you wanted to evolve.",
    "target_not_found": "⚠️ That edition is not available any more.",
    "not_same_player": "⚠️ Cards can only evolve into another edition of the same player.",
    "not_an_upgrade": "⚠️ The new edition is not a higher-rated card.",
    "already_owned": "ℹ️ You already own that edition.",
}


def _success_text(result: dict) -> str:
    old, new = result["old"], result["new"]
    lines = [
        "<b>🔁 CARD EVOLVED</b>",
        "",
        f"<blockquote><b>{_esc(_label(old))}  ➜  {_esc(_label(new))}</b></blockquote>",
        "<blockquote><b><i>"
        f"├ 💳 Paid        : 🪙 {result['cost']:,}\n"
        f"╰ 🪙 Balance     : {result['balance']:,}"
        "</i></b></blockquote>",
    ]
    notes = []
    if result.get("in_playing_xi"):
        notes.append("🏏 Playing XI spot kept")
    if result.get("was_captain"):
        notes.append("🧢 Captaincy kept")
    if result.get("upgrades_moved"):
        notes.append("⚙️ Upgrades moved: " + ", ".join(_esc(name) for name in result["upgrades_moved"]))
    if result.get("upgrades_dropped"):
        notes.append("⚠️ Not usable on this edition (unequipped): " + ", ".join(_esc(name) for name in result["upgrades_dropped"]))
    if notes:
        lines.append("<blockquote>" + "\n".join(notes) + "</blockquote>")
    return "\n".join(lines)


@register_callback("cevo_ok")
async def on_confirm(callback_query):
    parts = _parse(callback_query, 4)
    presser = int((callback_query.get("from") or {}).get("id") or 0)
    if not parts or not parts[0].lstrip("-").isdigit() or int(parts[0]) != presser:
        await _deny(callback_query, "This card evolution belongs to another player.")
        return
    if await user_in_match(presser):
        await _deny(callback_query, EVOLVE_BLOCKED_TEXT)
        return
    _owner, old_id, kind, entity_id = parts
    await _edit(callback_query, "<b>⏳ Evolving your card...</b>")
    result = await card_evolve.perform_evolve(presser, int(old_id), kind, int(entity_id))
    status = result.get("status")
    if status == "match_in_progress":
        await _edit(callback_query, f"<b>⚠️ {EVOLVE_BLOCKED_TEXT}</b>")
        await app.answer_callback_query(callback_query["id"])
        return
    if status == "success":
        await _edit(callback_query, _success_text(result))
        await app.answer_callback_query(callback_query["id"], "Card evolved.")
        return
    if status == "insufficient_balance":
        text = (
            "<b>❌ Not enough coins for this evolution.</b>\n"
            f"<blockquote><b>💳 Needed : 🪙 {result['cost']:,}\n🪙 Balance : {result['balance']:,}</b></blockquote>"
        )
    else:
        text = f"<b>{_esc(_FAILURES.get(status, '⚠️ The evolution could not be completed.'))}</b>"
    await _edit(callback_query, text)
    await app.answer_callback_query(callback_query["id"])
