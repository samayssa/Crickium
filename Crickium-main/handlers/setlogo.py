from __future__ import annotations

import html
import re

from app import app
from database.team_identity_repo import cancel_logo_request, create_logo_request, get_logo_request, set_logo_from_request
from handlers.registry import register, register_callback
from utils.user_identity import custom_emoji_html, get_user_identity, looks_like_custom_emoji_id


def _preview(logo_type: str, logo_id: str, fallback: str) -> str:
    if logo_type == "custom_emoji":
        return custom_emoji_html(logo_id, fallback or "🏷️")
    return fallback or "🎟️"


async def _build_pending(user_id: int, first_name: str | None, logo_type: str, logo_id: str, fallback: str, chat_id: int):
    identity = await get_user_identity(user_id, first_name)
    preview = _preview(logo_type, logo_id, fallback)
    token = await create_logo_request(user_id, logo_type, logo_id, fallback or "🏷️")
    text = (
        "╭━━〔 🎽 SET TEAM LOGO 〕━━╮\n\n"
        f"🎽 Team ➤ {identity['identity']}\n"
        f"🖼 Logo ➤ {preview}\n\n"
        "Are you sure you want to set this sticker/logo with your team name?"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ YES, SET LOGO", "callback_data": f"setlogo_yes:{token}", "style": "success"},
            {"text": "❌ CANCEL", "callback_data": f"setlogo_no:{token}", "style": "danger"},
        ]]
    }
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


@register("setlogo")
async def setlogo_command(message):
    chat_id = int(message["chat"]["id"])
    sender = message.get("from") or {}
    user_id = int(sender.get("id") or 0)
    first_name = sender.get("first_name")
    token_arg = str(message.get("text") or "").split(maxsplit=1)

    logo_type = None
    logo_id = None
    fallback = None

    if len(token_arg) > 1 and token_arg[1].strip():
        value = token_arg[1].strip().split()[0]
        if looks_like_custom_emoji_id(value):
            logo_type, logo_id, fallback = "custom_emoji", value, "⭐"
        else:
            logo_type, logo_id, fallback = "sticker", value, "🎟️"
    else:
        reply = message.get("reply_to_message") or {}
        emoji_ids = [e.get("custom_emoji_id") for e in (reply.get("entities") or []) if e.get("custom_emoji_id")]
        sticker = reply.get("sticker") or {}
        if emoji_ids:
            logo_type, logo_id, fallback = "custom_emoji", str(emoji_ids[0]), "⭐"
        elif sticker.get("file_id"):
            logo_type, logo_id, fallback = "sticker", str(sticker["file_id"]), str(sticker.get("emoji") or "🎟️")
        else:
            await app.send_message(
                chat_id,
                "⚠️ Use <code>/setlogo &lt;custom emoji ID or sticker file ID&gt;</code>\n"
                "or reply to a custom emoji/sticker with <code>/setlogo</code>.",
                parse_mode="HTML",
            )
            return

    await _build_pending(user_id, first_name, logo_type, logo_id, fallback, chat_id)


@register_callback("setlogo_yes")
async def setlogo_yes_callback(callback_query):
    token = str(callback_query.get("data") or "").split(":", 1)[1] if ":" in str(callback_query.get("data") or "") else ""
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    message = callback_query.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)

    row = await get_logo_request(token)
    if not row or int(row["user_id"]) != user_id:
        await app.answer_callback_query(callback_query.get("id"), "This logo request is no longer valid.", show_alert=True)
        return

    ok = await set_logo_from_request(token, user_id)
    if not ok:
        await app.answer_callback_query(callback_query.get("id"), "Could not set this logo.", show_alert=True)
        return

    identity = await get_user_identity(user_id)
    preview = _preview(str(row["logo_type"]), str(row["logo_id"]), str(row["logo_fallback"] or "🏷️"))
    text = (
        "✅ <b>TEAM LOGO SET SUCCESSFULLY</b>\n\n"
        f"🎽 Team ➤ {identity['identity']}\n"
        f"🖼 Logo ➤ {preview}\n\n"
        "Your team logo is now linked to your team name."
    )
    await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML")
    await app.answer_callback_query(callback_query.get("id"), "Team logo set.")


@register_callback("setlogo_no")
async def setlogo_no_callback(callback_query):
    token = str(callback_query.get("data") or "").split(":", 1)[1] if ":" in str(callback_query.get("data") or "") else ""
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    message = callback_query.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    await cancel_logo_request(token, user_id)
    await app.edit_message_text(
        chat_id,
        message_id,
        "❌ <b>TEAM LOGO SETUP CANCELLED</b>",
        parse_mode="HTML",
    )
    await app.answer_callback_query(callback_query.get("id"), "Cancelled.")
