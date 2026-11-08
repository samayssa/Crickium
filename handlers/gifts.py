"""Owner-only /distgift wizard for My Gifts campaigns."""
from __future__ import annotations

import html
import os
import re
import traceback
from datetime import datetime

from app import app
from config import ADMIN_USER_ID
from database import gifts_repo as repo
from database.query import fetch, fetchrow
from handlers.registry import register, register_callback
from services import gifts as gifts_service

PREFIX = "dgift"
CARD_RANGES = [(75, 80), (81, 85), (86, 90), (91, 95), (96, 99)]
COIN_AMOUNTS = [50_000, 100_000, 250_000, 500_000, 1_000_000]
RUBY_AMOUNTS = [50, 100, 250, 500, 1_000]
PACKS = [("bronze", "🥉 Bronze"), ("silver", "🥈 Silver"), ("gold", "🥇 Gold"), ("diamond", "💎 Diamond"), ("platinum", "👑 Platinum")]
SCHEDULES = [("30 minutes", 1800), ("1 hour", 3600), ("3 hours", 10800), ("6 hours", 21600), ("12 hours", 43200), ("24 hours", 86400)]


def _admin(user_id: int) -> bool:
    return bool(int(ADMIN_USER_ID or 0)) and int(user_id or 0) == int(ADMIN_USER_ID)


def _private(payload: dict) -> bool:
    return str((payload.get("chat") or {}).get("type") or "").lower() == "private"


def _button(label: str, action: str, gift_id: int, value: str | None = None) -> dict:
    parts = [PREFIX, action, str(int(gift_id))]
    if value is not None:
        parts.append(str(value))
    return {"text": label, "callback_data": ":".join(parts)}


def _keyboard(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def _coins_label(value: int) -> str:
    if value >= 1_000_000:
        return f"{value // 100_000 / 10:g}M coins"
    if value >= 100_000:
        return f"{value // 1000:,}K coins"
    return f"{value:,} coins"


def _frame(title: str, body: str) -> str:
    return (
        "<b>╭━━━〔 🏏 CRICKIUM · MY GIFTS 〕━━━╮</b>\n\n"
        f"<b>{title}</b>\n\n{body}\n\n"
        "<i>Owner control panel · Private distribution</i>\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _type_prompt(gift_id: int) -> tuple[str, dict]:
    text = _frame("🎁 Create a gift drop", "Choose the reward package you want to prepare for every Crickium user.")
    rows = [
        [_button("🏏 Player", "type", gift_id, "player"), _button("🃏 Card Gift", "type", gift_id, "card")],
        [_button("🪙 Coins Gift", "type", gift_id, "coins"), _button("💎 Rubies Gift", "type", gift_id, "rubies")],
        [_button("🎁 Multiple Rewards", "type", gift_id, "multiple")],
        [_button("✖ Cancel", "cancel", gift_id)],
    ]
    return text, _keyboard(rows)


def _range_prompt(gift_id: int, *, multiple: bool = False) -> tuple[str, dict]:
    text = _frame("🃏 Choose player OVR", ("Select the player-card rating range. Next, you’ll choose coins and Rubies." if multiple else "Every recipient gets one randomly selected, eligible player card from this OVR range."))
    rows = [[_button(f"{lo}–{hi} OVR", "range", gift_id, f"{lo}-{hi}")] for lo, hi in CARD_RANGES]
    rows.append([_button("↩ Back", "backtype", gift_id), _button("✖ Cancel", "cancel", gift_id)])
    return text, _keyboard(rows)


def _coins_prompt(gift_id: int) -> tuple[str, dict]:
    text = _frame("🪙 Choose coins", "Select the coin amount each user will receive.")
    rows = [[_button(_coins_label(amount), "coins", gift_id, str(amount))] for amount in COIN_AMOUNTS]
    rows.append([_button("✖ Cancel", "cancel", gift_id)])
    return text, _keyboard(rows)


def _rubies_prompt(gift_id: int) -> tuple[str, dict]:
    text = _frame("💎 Choose Rubies", "Rubies are Crickium’s premium wallet currency. Select the amount for every user.")
    rows = [[_button(f"💎 {amount:,} Rubies", "rubies", gift_id, str(amount))] for amount in RUBY_AMOUNTS]
    rows.append([_button("✖ Cancel", "cancel", gift_id)])
    return text, _keyboard(rows)


def _yes_no_prompt(gift_id: int, step: str, title: str, body: str) -> tuple[str, dict]:
    return _frame(title, body), _keyboard([
        [_button("✅ Yes", step, gift_id, "yes"), _button("No thanks", step, gift_id, "no")],
        [_button("✖ Cancel", "cancel", gift_id)],
    ])


def _packs_prompt(gift_id: int) -> tuple[str, dict]:
    return _yes_no_prompt(gift_id, "packs", "📦 Add a pack gift?", "Would you like to include one player pack in this distribution for every user?")


def _theme_prompt(gift_id: int) -> tuple[str, dict]:
    return _frame("✨ Choose the gift atmosphere", "Pick how this gift will appear in the Mini App."), _keyboard([
        [_button("🪔 Diwali Festival", "theme", gift_id, "diwali")],
        [_button("🌿 Normal Gift", "theme", gift_id, "normal")],
        [_button("✖ Cancel", "cancel", gift_id)],
    ])


def _image_prompt(gift_id: int) -> tuple[str, dict]:
    return _yes_no_prompt(gift_id, "image", "🖼 Add a festival banner?", "Would you like to attach an image banner to this Diwali gift? Choose Yes to upload an image directly in this chat.")


def _pack_type_prompt(gift_id: int) -> tuple[str, dict]:
    rows = [[_button(label, "pack", gift_id, key)] for key, label in PACKS]
    rows.append([_button("↩ Skip pack", "pack", gift_id, "none")])
    rows.append([_button("✖ Cancel", "cancel", gift_id)])
    return _frame("📦 Select the pack", "Choose one pack to add to every user’s gift. The pack will be credited to their existing Pack Shop inventory."), _keyboard(rows)


def _preview_text(campaign: dict) -> str:
    cfg = gifts_service.json_value(campaign.get("reward_config"), {})
    reward_type = campaign.get("reward_type") or "gift"
    lines = [f"🧩 <b>Type:</b> {html.escape(str(reward_type).title())}"]
    if cfg.get("player"):
        p = cfg["player"]
        lines.append(f"🏏 <b>Player:</b> {html.escape(str(p.get('name') or 'Selected player'))} · OVR {max(int(p.get('bat_level') or 0), int(p.get('bowl_level') or 0))}")
    if cfg.get("player_range"):
        lines.append(f"🃏 <b>Random card:</b> {int(cfg['player_range'][0])}–{int(cfg['player_range'][1])} OVR")
    if cfg.get("coins"):
        lines.append(f"🪙 <b>Coins:</b> {int(cfg['coins']):,}")
    if cfg.get("rubies"):
        lines.append(f"💎 <b>Rubies:</b> {int(cfg['rubies']):,}")
    if cfg.get("pack_key"):
        lines.append(f"📦 <b>Pack:</b> {html.escape(str(cfg['pack_key']).title())}")
    theme = str(campaign.get("theme") or "normal")
    lines.append(f"🎨 <b>Theme:</b> {'Diwali Festival' if theme == 'diwali' else 'Normal'}")
    lines.append(f"🖼 <b>Banner:</b> {'Attached' if campaign.get('image_file_id') else 'Not added'}")
    lines.append("\n<i>Users will receive an entry in My Gifts. No Telegram announcement or DM will be sent.</i>")
    return _frame("🎁 Gift ready to distribute", "\n".join(lines))


async def _edit(campaign: dict, text: str, keyboard: dict | None) -> None:
    chat_id, message_id = int(campaign["admin_chat_id"]), int(campaign["admin_message_id"])
    if campaign.get("admin_message_is_photo"):
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)


async def _step(campaign: dict, step: str, prompt: tuple[str, dict]) -> None:
    await repo.update_draft(int(campaign["gift_id"]), step=step)
    fresh = await repo.get_campaign(int(campaign["gift_id"]))
    if fresh:
        await _edit(fresh, prompt[0], prompt[1])


async def _after_reward_choice(campaign: dict, cfg: dict, reward_type: str) -> None:
    await repo.update_draft(int(campaign["gift_id"]), reward_type=reward_type, reward_config=cfg, step="choose_packs")
    await _edit(campaign, *_packs_prompt(int(campaign["gift_id"])))


async def _make_ready(campaign: dict) -> None:
    updated = await repo.update_draft(int(campaign["gift_id"]), step="ready", status="ready")
    if updated:
        await _edit(updated, _preview_text(updated), _keyboard([
            [_button("🚀 Direct Distribute", "direct", int(updated["gift_id"]))],
            [_button("⏰ Schedule", "schedule", int(updated["gift_id"]))],
            [_button("✖ Cancel Gift", "cancel", int(updated["gift_id"]))],
        ]))


def _command_args(payload: dict) -> list[str]:
    words = str(payload.get("text") or "").strip().split()
    return [word.lower() for word in words[1:]] if words else []


def _gift_management_view(rows: list[dict], offset: int, *, has_more: bool) -> tuple[str, dict]:
    if not rows:
        return _frame("🗂 Active gift drops", "There are no distributed gifts currently visible in users’ My Gifts sections."), _keyboard([])

    lines = ["Review each live gift drop. Open counts include users who opened the reveal, including those who have already claimed it."]
    keyboard_rows = []
    for row in rows:
        gift_id = int(row["gift_id"])
        title = html.escape(str(row.get("title") or "Gift"))
        theme = "🪔 Diwali" if row.get("theme") == "diwali" else "🎁 Normal"
        total = int(row.get("recipients") or row.get("recipient_count") or 0)
        opened = int(row.get("opened_count") or 0)
        claimed = int(row.get("claimed_count") or 0)
        lines.append(
            f"<b>#{gift_id} · {title}</b>  {theme}\n"
            f"👥 Distributed: <b>{total:,}</b> users\n"
            f"👀 Opened: <b>{opened:,}</b> users\n"
            f"✅ Claimed: <b>{claimed:,}</b> users"
        )
        keyboard_rows.append([_button(f"🗑 Remove this gift · #{gift_id}", "remove", gift_id)])

    nav = []
    if offset > 0:
        nav.append(_button("‹ Newer", "listpage", 0, str(max(0, offset - 8))))
    if has_more:
        nav.append(_button("Older ›", "listpage", 0, str(offset + 8)))
    if nav:
        keyboard_rows.append(nav)
    return _frame("🗂 Active gift drops", "\n\n".join(lines)), _keyboard(keyboard_rows)


async def _show_gift_management(chat_id: int, *, message_id: int | None = None, offset: int = 0) -> None:
    rows = await repo.list_distributed_campaigns(int(ADMIN_USER_ID), limit=9, offset=offset)
    has_more = len(rows) > 8
    rows = rows[:8]
    text, keyboard = _gift_management_view(rows, offset, has_more=has_more)
    if message_id:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


async def _gift_management_callback(query: dict, action: str, gift_id: int, value: str, chat_id: int, message_id: int) -> bool:
    """Handle owner-only gift-list callbacks before draft-message validation."""
    if action == "listpage":
        try:
            offset = max(0, int(value or 0))
        except ValueError:
            offset = 0
        await app.answer_callback_query(query.get("id"))
        await _show_gift_management(chat_id, message_id=message_id, offset=offset)
        return True

    if action in {"remove", "confirmremove", "cancelremove"}:
        details = await repo.distributed_campaign_details(gift_id, int(ADMIN_USER_ID))
        if not details or str(details.get("status") or "distributed") != "distributed":
            await app.answer_callback_query(query.get("id"), "This gift is already removed or is no longer active.", show_alert=True)
            return True

        if action == "remove":
            await app.answer_callback_query(query.get("id"))
            body = (
                f"You are about to remove <b>{html.escape(str(details.get('title') or 'this gift'))}</b> (#<b>{gift_id}</b>) from every user’s My Gifts list.\n\n"
                f"👥 Recipients: <b>{int(details.get('recipients') or 0):,}</b>\n"
                f"👀 Already opened: <b>{int(details.get('opened_count') or 0):,}</b>\n"
                f"✅ Already claimed: <b>{int(details.get('claimed_count') or 0):,}</b>\n\n"
                "This hides the gift for everyone. Rewards already claimed stay in users’ accounts; unclaimed rewards will no longer be claimable."
            )
            await app.edit_message_text(chat_id, message_id, _frame("⚠️ Confirm gift removal", body), parse_mode="HTML", reply_markup=_keyboard([
                [_button("✅ Confirm remove", "confirmremove", gift_id)],
                [_button("↩ Keep this gift", "cancelremove", gift_id)],
            ]))
            return True

        if action == "cancelremove":
            await app.answer_callback_query(query.get("id"), "Removal cancelled.")
            await _show_gift_management(chat_id, message_id=message_id, offset=0)
            return True

        removed = await repo.remove_distributed_campaign(gift_id, int(ADMIN_USER_ID))
        await app.answer_callback_query(query.get("id"), "Gift removed from My Gifts." if removed else "Gift was already removed.")
        if removed:
            final = (
                f"<b>{html.escape(str(removed['title']))}</b> (#<b>{gift_id}</b>) is now hidden from every user’s My Gifts section.\n\n"
                f"👥 Recipients: <b>{removed['recipients']:,}</b>\n"
                f"👀 Opened: <b>{removed['opened_count']:,}</b>\n"
                f"✅ Claimed: <b>{removed['claimed_count']:,}</b>\n\n"
                "Already-claimed rewards remain in users’ accounts. Unclaimed copies are no longer available."
            )
            await app.edit_message_text(chat_id, message_id, _frame("🗑 Gift removed", final), parse_mode="HTML", reply_markup=_keyboard([
                [_button("↩ Back to active gifts", "listpage", 0, "0")]
            ]))
        else:
            await _show_gift_management(chat_id, message_id=message_id, offset=0)
        return True
    return False


def _mygift_app_url() -> str:
    """Build the official Telegram Main Mini App deep link to My Gifts."""
    username = str(os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot")).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        username = "CrickiumBot"
    return f"https://t.me/{username}?startapp=mygifts&mode=fullscreen"


def _public_gift_list_text(first_name: str, rows: list[dict]) -> str:
    greeting = html.escape(str(first_name or "Player"))
    lines = [
        "<b>╭━━━〔 🎁 CRICKIUM · MY GIFTS 〕━━━╮</b>",
        "",
        f"Welcome, <b>{greeting}</b>! Here are your available gifts.",
        "",
    ]
    if not rows:
        lines.extend([
            "<b>✨ No unclaimed gifts right now.</b>",
            "",
            "Keep an eye on Crickium. A new surprise may arrive soon!",
        ])
    else:
        for row in rows[:15]:
            reward = gifts_service.json_value(row.get("reward"), {})
            reward_labels = []
            if int(reward.get("coins") or 0) > 0:
                reward_labels.append("🪙 Coins")
            if int(reward.get("rubies") or 0) > 0:
                reward_labels.append("💎 Rubies")
            if isinstance(reward.get("player"), dict):
                reward_labels.append("🏏 Player Card")
            if reward.get("pack_key"):
                reward_labels.append("📦 Pack")
            if not reward_labels:
                reward_labels.append("🎁 Surprise Reward")

            title = "Happy Diwali" if str(row.get("theme") or "normal") == "diwali" else str(row.get("title") or "A Gift for You")
            status = "Opened · ready to claim" if str(row.get("status")) == "opened" else "Ready to open"
            lines.extend([
                f"<b>🎁 {html.escape(title[:90])}</b>",
                f"   {' · '.join(reward_labels)}",
                f"   <i>{status}</i>",
                "",
            ])
        if len(rows) >= 15:
            lines.append("<i>Showing your 15 newest available gifts.</i>")
            lines.append("")

    lines.extend([
        "Tap below to open the My Gifts section in the Crickium Mini App.",
        "",
        "<b>╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯</b>",
    ])
    return "\n".join(lines)


@register("mygift")
async def mygift_command(payload: dict) -> None:
    """Show the invoking user's available gifts in any bot chat."""
    user = payload.get("from") or {}
    user_id = int(user.get("id") or 0)
    chat = payload.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    if not user_id or not chat_id:
        return

    rows = await repo.list_user_available_gifts(user_id, limit=15)
    text = _public_gift_list_text(str(user.get("first_name") or "Player"), rows)
    keyboard = _keyboard([[
        {
            "text": "🎁 Open MyGift in App",
            "url": _mygift_app_url(),
            "style": "primary",
        }
    ]])
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)


@register("gift")
async def gift_command(payload: dict) -> None:
    user_id = int((payload.get("from") or {}).get("id") or 0)
    chat_id = int((payload.get("chat") or {}).get("id") or 0)
    if not _admin(user_id) or not _private(payload) or chat_id != user_id:
        return
    args = _command_args(payload)
    if args and args[0] in {"remove", "list", "manage"}:
        await _show_gift_management(chat_id)
        return
    await app.send_message(
        chat_id,
        _frame("🎁 Gift management", "Use <code>/gift remove</code> to list active gift drops, review open counts, and remove a selected gift from every user’s My Gifts section.\n\nUse <code>/distgift</code> to prepare a new gift distribution."),
        parse_mode="HTML",
    )


@register("distgift")
async def distgift(payload: dict) -> None:
    user_id = int((payload.get("from") or {}).get("id") or 0)
    chat_id = int((payload.get("chat") or {}).get("id") or 0)
    if not _admin(user_id) or not _private(payload) or chat_id != user_id:
        return
    args = _command_args(payload)
    if args and args[0] in {"remove", "list", "manage"}:
        await _show_gift_management(chat_id)
        return
    campaign = await repo.create_draft(user_id, chat_id)
    if not campaign:
        await app.send_message(chat_id, "⚠️ I couldn’t start the gift setup. Please try again.")
        return
    text, keyboard = _type_prompt(int(campaign["gift_id"]))
    message = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
    message_id = int((message or {}).get("message_id") or (message or {}).get("id") or 0)
    if message_id:
        await repo.save_message(int(campaign["gift_id"]), chat_id, message_id)


@register_callback(PREFIX)
async def gift_callback(query: dict) -> None:
    user_id = int((query.get("from") or {}).get("id") or 0)
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    if not _admin(user_id) or str(chat.get("type") or "").lower() != "private" or chat_id != user_id:
        await app.answer_callback_query(query.get("id"), "Owner-only control.", show_alert=True)
        return
    parts = str(query.get("data") or "").split(":")
    if len(parts) < 3:
        await app.answer_callback_query(query.get("id"), "Invalid gift action.", show_alert=True)
        return
    try:
        gift_id = int(parts[2])
    except ValueError:
        await app.answer_callback_query(query.get("id"), "Invalid gift action.", show_alert=True)
        return
    action = parts[1]
    value = parts[3] if len(parts) > 3 else ""
    if action in {"listpage", "remove", "confirmremove", "cancelremove"}:
        if await _gift_management_callback(query, action, gift_id, value, chat_id, message_id):
            return
    campaign = await repo.get_campaign(gift_id)
    if not campaign or int(campaign.get("created_by") or 0) != user_id or int(campaign.get("admin_chat_id") or 0) != chat_id or int(campaign.get("admin_message_id") or 0) != message_id:
        await app.answer_callback_query(query.get("id"), "This gift setup has expired.", show_alert=True)
        return
    await app.answer_callback_query(query.get("id"))
    cfg = gifts_service.json_value(campaign.get("reward_config"), {})

    if action == "pickplayer":
        try:
            index = int(value)
            choices = cfg.get("_player_choices") or []
            selected = choices[index]
        except (ValueError, IndexError, TypeError):
            await app.answer_callback_query(query.get("id"), "That player option has expired.", show_alert=True)
            return
        cfg.pop("_player_choices", None)
        cfg["player"] = selected
        await repo.update_draft(gift_id, reward_config=cfg, step="choose_packs")
        fresh = await repo.get_campaign(gift_id)
        await _edit(fresh, *_packs_prompt(gift_id))
        return

    if action == "playeragain":
        await repo.update_draft(gift_id, step="awaiting_player")
        fresh = await repo.get_campaign(gift_id)
        await _edit(fresh, _frame("🏏 Select a player", "Send the exact player name in this chat. I’ll show a matching card for confirmation."), _keyboard([[_button("↩ Back", "backtype", gift_id), _button("✖ Cancel", "cancel", gift_id)]]))
        return

    if action == "cancel":
        await repo.cancel_draft(gift_id, user_id)
        try:
            await _edit(campaign, _frame("Gift cancelled", "This gift setup has been cancelled."), None)
        except Exception:
            pass
        return

    if campaign.get("status") == "ready" and action not in {"direct", "schedule", "delay", "preview"}:
        await app.answer_callback_query(query.get("id"), "This gift is already ready.", show_alert=True)
        return

    if action == "type":
        reward_type = value
        if reward_type == "player":
            await repo.update_draft(gift_id, reward_type=reward_type, step="awaiting_player")
            await _edit(campaign, _frame("🏏 Select a player", "Send the exact player name in this chat. I’ll show a matching card for confirmation."), _keyboard([[_button("↩ Back", "backtype", gift_id), _button("✖ Cancel", "cancel", gift_id)]]))
        elif reward_type == "card":
            await repo.update_draft(gift_id, reward_type=reward_type)
            await _step(campaign, "choose_card_range", _range_prompt(gift_id))
        elif reward_type == "coins":
            await repo.update_draft(gift_id, reward_type=reward_type)
            await _step(campaign, "choose_coins", _coins_prompt(gift_id))
        elif reward_type == "rubies":
            await repo.update_draft(gift_id, reward_type=reward_type)
            await _step(campaign, "choose_rubies", _rubies_prompt(gift_id))
        elif reward_type == "multiple":
            await repo.update_draft(gift_id, reward_type=reward_type)
            await _step(campaign, "choose_card_range", _range_prompt(gift_id, multiple=True))
        else:
            await app.answer_callback_query(query.get("id"), "Choose a valid reward type.", show_alert=True)
        return

    if action == "backtype":
        await _step(campaign, "choose_type", _type_prompt(gift_id))
        return

    if action == "range":
        try:
            lo, hi = [int(x) for x in value.split("-", 1)]
        except Exception:
            await app.answer_callback_query(query.get("id"), "Invalid player range.", show_alert=True)
            return
        if (lo, hi) not in CARD_RANGES:
            await app.answer_callback_query(query.get("id"), "Invalid player range.", show_alert=True)
            return
        cfg["player_range"] = [lo, hi]
        if campaign.get("reward_type") == "multiple":
            await repo.update_draft(gift_id, reward_config=cfg, step="choose_coins")
            await _edit(campaign, *_coins_prompt(gift_id))
        else:
            await _after_reward_choice(campaign, cfg, "card")
        return

    if action == "coins":
        try:
            amount = int(value)
        except ValueError:
            amount = 0
        if amount not in COIN_AMOUNTS:
            await app.answer_callback_query(query.get("id"), "Invalid coin amount.", show_alert=True)
            return
        cfg["coins"] = amount
        if campaign.get("reward_type") == "multiple":
            await repo.update_draft(gift_id, reward_config=cfg, step="choose_rubies")
            await _edit(campaign, *_rubies_prompt(gift_id))
        else:
            await _after_reward_choice(campaign, cfg, "coins")
        return

    if action == "rubies":
        try:
            amount = int(value)
        except ValueError:
            amount = 0
        if amount not in RUBY_AMOUNTS:
            await app.answer_callback_query(query.get("id"), "Invalid Rubies amount.", show_alert=True)
            return
        cfg["rubies"] = amount
        await _after_reward_choice(campaign, cfg, "multiple" if campaign.get("reward_type") == "multiple" else "rubies")
        return

    if action == "packs":
        if value == "yes":
            await _step(campaign, "choose_pack", _pack_type_prompt(gift_id))
        else:
            cfg.pop("pack_key", None)
            await repo.update_draft(gift_id, reward_config=cfg)
            await _step(campaign, "choose_theme", _theme_prompt(gift_id))
        return

    if action == "pack":
        if value == "none":
            cfg.pop("pack_key", None)
        elif value in {key for key, _ in PACKS}:
            cfg["pack_key"] = value
        else:
            await app.answer_callback_query(query.get("id"), "Invalid pack selection.", show_alert=True)
            return
        await repo.update_draft(gift_id, reward_config=cfg)
        await _step(campaign, "choose_theme", _theme_prompt(gift_id))
        return

    if action == "theme":
        if value not in {"normal", "diwali"}:
            await app.answer_callback_query(query.get("id"), "Invalid theme.", show_alert=True)
            return
        await repo.update_draft(gift_id, theme=value, title=("Happy Diwali" if value == "diwali" else "A Gift for You"), step="choose_image")
        if value == "diwali":
            await _edit(campaign, *_image_prompt(gift_id))
        else:
            await _make_ready(campaign)
        return

    if action == "image":
        if value == "yes":
            await repo.update_draft(gift_id, step="awaiting_image")
            await _edit(campaign, _frame("🖼 Upload the festival banner", "Send one image in this chat as a normal photo. No command and no reply are needed."), _keyboard([[_button("Skip image", "skipimage", gift_id)], [_button("✖ Cancel", "cancel", gift_id)]]))
        else:
            await _make_ready(campaign)
        return

    if action == "skipimage":
        await _make_ready(campaign)
        return

    if action == "schedule":
        rows = [[_button(f"⏳ {label}", "delay", gift_id, str(seconds))] for label, seconds in SCHEDULES]
        rows.append([_button("↩ Back", "preview", gift_id), _button("✖ Cancel", "cancel", gift_id)])
        await _edit(campaign, _frame("⏰ Schedule distribution", "Choose when this prepared gift should automatically become available to all users."), _keyboard(rows))
        return

    if action == "preview":
        await _edit(campaign, _preview_text(campaign), _keyboard([
            [_button("🚀 Direct Distribute", "direct", gift_id)],
            [_button("⏰ Schedule", "schedule", gift_id)],
            [_button("✖ Cancel Gift", "cancel", gift_id)],
        ]))
        return

    if action == "delay":
        try:
            seconds = int(value)
        except ValueError:
            seconds = 0
        if seconds not in {x[1] for x in SCHEDULES}:
            await app.answer_callback_query(query.get("id"), "Invalid schedule.", show_alert=True)
            return
        scheduled = await repo.set_schedule(gift_id, seconds)
        if scheduled:
            await _edit(scheduled, _frame("⏰ Gift scheduled", f"This gift will be distributed automatically in <b>{html.escape(next(x[0] for x in SCHEDULES if x[1] == seconds))}</b>. No user message will be sent."), None)
            gifts_service.wake_worker()
        return

    if action == "direct":
        await _edit(campaign, _frame("🚀 Distributing gifts…", "Preparing individual rewards for every registered Crickium user. No Telegram messages will be sent to users."), None)
        async def progress(done: int, total: int) -> None:
            # Avoid excessive Telegram edits during large distributions.
            if done == total or done % 1000 == 0:
                if campaign.get("admin_message_is_photo"):
                    await app.edit_message_caption(chat_id, message_id, _frame("🚀 Distributing gifts…", f"Assigned <b>{done:,}</b> of <b>{total:,}</b> users."), parse_mode="HTML")
                else:
                    await app.edit_message_text(chat_id, message_id, _frame("🚀 Distributing gifts…", f"Assigned <b>{done:,}</b> of <b>{total:,}</b> users."), parse_mode="HTML")
        try:
            result = await gifts_service.distribute_campaign(gift_id, progress=progress)
            if result.get("status") == "distributed":
                final_text = _frame("✅ Gift distributed", f"The gift is now available in My Gifts for <b>{int(result.get('recipients') or 0):,}</b> users. No announcement or DM was sent.")
                if campaign.get("admin_message_is_photo"):
                    await app.edit_message_caption(chat_id, message_id, final_text, parse_mode="HTML")
                else:
                    await app.edit_message_text(chat_id, message_id, final_text, parse_mode="HTML")
            else:
                fail_text = _frame("⚠️ Distribution not completed", f"Status: <code>{html.escape(str(result.get('status')))}</code>. You can retry after resolving the issue.")
                if campaign.get("admin_message_is_photo"):
                    await app.edit_message_caption(chat_id, message_id, fail_text, parse_mode="HTML")
                else:
                    await app.edit_message_text(chat_id, message_id, fail_text, parse_mode="HTML")
        except Exception as exc:
            print(f"[gifts] direct distribution failed: {type(exc).__name__}: {exc}")
            fail_text = _frame("⚠️ Distribution failed", "The campaign was not confirmed as complete. Check the Railway logs and retry after fixing the database issue.")
            if campaign.get("admin_message_is_photo"):
                await app.edit_message_caption(chat_id, message_id, fail_text, parse_mode="HTML")
            else:
                await app.edit_message_text(chat_id, message_id, fail_text, parse_mode="HTML")
        return

    await app.answer_callback_query(query.get("id"), "This gift action is not available.", show_alert=True)


async def handle_distgift_message(payload: dict) -> bool:
    """Consume admin's plain text player search or direct image upload."""
    user_id = int((payload.get("from") or {}).get("id") or 0)
    chat = payload.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    if not _admin(user_id) or str(chat.get("type") or "").lower() != "private" or chat_id != user_id:
        return False
    campaign = await repo.draft_for_admin(user_id, chat_id)
    if not campaign:
        return False
    step = str(campaign.get("step") or "")
    gift_id = int(campaign["gift_id"])

    if step == "awaiting_image":
        photo = payload.get("photo") or {}
        document = payload.get("document") or {}
        file_id = str(photo.get("file_id") or "")
        if not file_id and str(document.get("mime_type") or "").lower().startswith("image/"):
            file_id = str(document.get("file_id") or "")
        if not file_id:
            await app.send_message(chat_id, "🖼 Please send the banner as a photo or image file, or tap Skip image on the gift setup.")
            return True
        await repo.update_draft(gift_id, image_file_id=file_id)
        await app.delete_message(chat_id, int(campaign.get("admin_message_id") or 0))
        updated = await repo.get_campaign(gift_id)
        caption = _preview_text(updated)
        sent = await app.send_photo(chat_id, file_id, caption=caption, parse_mode="HTML", reply_markup=_keyboard([
            [_button("🚀 Direct Distribute", "direct", gift_id)],
            [_button("⏰ Schedule", "schedule", gift_id)],
            [_button("✖ Cancel Gift", "cancel", gift_id)],
        ]))
        sent_id = int((sent or {}).get("message_id") or (sent or {}).get("id") or 0)
        if sent_id:
            await repo.save_message(gift_id, chat_id, sent_id, is_photo=True)
            await repo.update_draft(gift_id, status="ready", step="ready")
        return True

    if step == "awaiting_player":
        name = str(payload.get("text") or "").strip()
        if not name:
            await app.send_message(chat_id, "🏏 Send the player’s name as text.")
            return True
        if len(name) > 80:
            await app.send_message(chat_id, "Please keep the player name under 80 characters.")
            return True
        rows = await fetch(
            """SELECT player_id,name,country,role,bat_level,bowl_level,batting_hand,bowling_hand,'global'::text AS player_kind
               FROM players WHERE LOWER(name)=LOWER($1) LIMIT 5;""", name,
        )
        special = await fetch(
            """SELECT (-special_player_id)::bigint AS player_id,special_player_id AS special_edition_id,
                      name,country,role,bat_level,bowl_level,batting_hand,bowling_hand,edition,
                      'special'::text AS player_kind,TRUE AS is_special
               FROM special_edition_players WHERE LOWER(name)=LOWER($1) LIMIT 5;""", name,
        )
        candidates = [gifts_service.card_snapshot(dict(row)) for row in [*rows, *special]]
        if not candidates:
            await app.send_message(chat_id, f"I couldn’t find <b>{html.escape(name)}</b>. Send the exact player name again.", parse_mode="HTML")
            return True
        if len(candidates) == 1:
            cfg = gifts_service.json_value(campaign.get("reward_config"), {})
            cfg["player"] = candidates[0]
            await repo.update_draft(gift_id, reward_config=cfg, step="choose_packs")
            fresh = await repo.get_campaign(gift_id)
            await _edit(fresh, *_packs_prompt(gift_id))
            return True
        rows_kb = []
        for index, candidate in enumerate(candidates):
            overall = max(int(candidate.get("bat_level") or 0), int(candidate.get("bowl_level") or 0))
            edition = f" · {candidate.get('edition')}" if candidate.get("edition") else ""
            rows_kb.append([_button(f"{candidate['name']} · {overall}{edition}", "pickplayer", gift_id, str(index))])
        # Store the candidate set in the draft JSON so buttons remain restart-safe.
        cfg = gifts_service.json_value(campaign.get("reward_config"), {})
        cfg["_player_choices"] = candidates
        await repo.update_draft(gift_id, reward_config=cfg, step="choose_player")
        fresh = await repo.get_campaign(gift_id)
        await _edit(fresh, _frame("🏏 Choose the player card", f"I found <b>{len(candidates)}</b> matching cards for <b>{html.escape(name)}</b>. Choose the exact edition."), _keyboard(rows_kb + [[_button("↩ Search again", "playeragain", gift_id), _button("✖ Cancel", "cancel", gift_id)]]))
        return True

    # Ignore unrelated messages while the wizard is on a button-driven step.
    return False

