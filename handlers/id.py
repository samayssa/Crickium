from __future__ import annotations

import html

from app import app
from handlers.registry import register


def _profile_name(user: dict | None, fallback: str = "Unknown") -> str:
    user = user or {}
    parts = [str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()]
    name = " ".join(p for p in parts if p)
    username = str(user.get("username") or "").strip()
    if name:
        return name
    if username:
        return f"@{username}"
    return fallback


def _chat_kind(chat: dict) -> str:
    return str(chat.get("type") or "").lower()


                                                                               
                                                                            
                                                                         
                                                                           
EMOJI_TAG_STYLE = "tg-emoji"


def _utf16_slice(text: str, offset: int, length: int) -> str:
                                                                                        
    data = text.encode("utf-16-le")
    return data[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


def _entity_custom_emojis(msg: dict) -> list[tuple[str, str]]:
    """Return [(custom_emoji_id, fallback_emoji_char), ...] in message order."""
    text = str(msg.get("text") or msg.get("caption") or "")
    entities = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
    found = []
    for entity in entities:
        value = entity.get("custom_emoji_id")
        if not value:
            continue
        fallback = ""
        try:
            fallback = _utf16_slice(text, int(entity.get("offset") or 0), int(entity.get("length") or 0))
        except Exception:
            fallback = ""
        found.append((str(value), fallback.strip() or "⭐"))
    return found


def _entity_custom_emoji_ids(msg: dict) -> list[str]:
    return [eid for eid, _ in _entity_custom_emojis(msg)]


def _render_custom_emoji(eid: str, fallback: str) -> str:
    safe_id = html.escape(eid, quote=True)
    safe_fb = html.escape(fallback)
    if EMOJI_TAG_STYLE == "emoji":
        return f'<emoji id="{safe_id}">{safe_fb}</emoji>'
    return f'<tg-emoji emoji-id="{safe_id}">{safe_fb}</tg-emoji>'


def _content_type(msg: dict) -> str:
    if msg.get("sticker"):
        return "STICKER"
    if _entity_custom_emoji_ids(msg):
        return "CUSTOM EMOJI"
    if msg.get("voice"):
        return "VOICE"
    if msg.get("photo"):
        return "PHOTO"
    if msg.get("document"):
        return "DOCUMENT"
    if msg.get("text"):
        return "TEXT"
    return "MESSAGE"


async def _lookup_username(query: str):
    value = str(query or "").strip()
    if not value:
        return None
    value = value[1:] if value.startswith("@") else value
    try:
        return await app._client.get_users(value)
    except Exception as exc:
        print(f"[id] get_users lookup failed for {value!r}: {exc!r}")
        return None


def _user_lines(title: str, user: dict) -> list[str]:
    name = html.escape(_profile_name(user))
    username = str(user.get("username") or "").strip()
    lines = [f"👤 {html.escape(title)} ➤ {name}"]
    if username:
        lines.append(f"▫️ Username ➤ @{html.escape(username)}")
    lines.append(f"🆔 User ID ➤ <code>{int(user.get('id') or 0)}</code>")
    return lines


@register("id")
async def id_command(message):
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = int(chat.get("id") or 0)
    chat_kind = _chat_kind(chat)
    lines = ["╭━━〔 🆔 ID INFO 〕━━╮", ""]
    lines.extend(_user_lines("Sender", sender))

    if chat_kind not in {"private", "chattype.private"}:
        title = chat.get("title") or chat.get("username") or "Group Chat"
        lines.append(f"💬 Chat ➤ {html.escape(str(title))}")
        lines.append(f"🆔 Chat ID ➤ <code>{chat_id}</code>")

    parts = str(message.get("text") or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        query = parts[1].strip().split()[0]
        target = await _lookup_username(query)
        if target is None:
            lines += ["", f"🔎 Username ➤ <code>{html.escape(query)}</code>", "⚠️ User could not be resolved by Telegram."]
        else:
            target_dict = {
                "id": getattr(target, "id", 0),
                "first_name": getattr(target, "first_name", None),
                "last_name": getattr(target, "last_name", None),
                "username": getattr(target, "username", None),
            }
            lines += ["", *(_user_lines("Requested User", target_dict))]

    reply = message.get("reply_to_message")
    if reply:
        lines += ["", "↪️ Reply Target"]
        reply_user = reply.get("from") or {}
        if reply_user.get("id"):
            lines.extend(_user_lines("Profile", reply_user))

        content_type = _content_type(reply)
        lines.append(f"📦 Content ➤ {content_type}")

        custom_emojis = _entity_custom_emojis(reply)
        emoji_ids = [eid for eid, _ in custom_emojis]
        sticker = reply.get("sticker") or {}
        voice = reply.get("voice") or {}
        if custom_emojis:
            lines.append(f"✨ Custom Emojis ➤ {len(custom_emojis)}")
            for index, (eid, fallback) in enumerate(custom_emojis, start=1):
                rendered = _render_custom_emoji(eid, fallback)
                lines.append(f"{index}. {rendered} ➤ <code>{html.escape(eid)}</code>")
        if sticker:
            if sticker.get("file_unique_id"):
                lines.append(f"🎟 Sticker File Unique ID ➤ <code>{html.escape(str(sticker['file_unique_id']))}</code>")
            if sticker.get("file_id"):
                lines.append(f"🎟 Sticker File ID ➤ <code>{html.escape(str(sticker['file_id']))}</code>")
            if sticker.get("emoji"):
                lines.append(f"🙂 Sticker Emoji ➤ {html.escape(str(sticker['emoji']))}")
        if voice:
            if voice.get("file_unique_id"):
                lines.append(f"🎙 Voice File Unique ID ➤ <code>{html.escape(str(voice['file_unique_id']))}</code>")
            if voice.get("file_id"):
                lines.append(f"🎙 Voice File ID ➤ <code>{html.escape(str(voice['file_id']))}</code>")

        if not reply_user.get("id") and not emoji_ids and not sticker:
            lines.append("ℹ️ No sender/user metadata was available for this reply.")

    lines += ["", "╰━━━━━━━━━━━━━━━━━━╯"]
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
