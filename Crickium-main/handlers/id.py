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


def _entity_custom_emoji_ids(msg: dict) -> list[str]:
    ids = []
    for entity in msg.get("entities") or []:
        value = entity.get("custom_emoji_id")
        if value:
            ids.append(str(value))
    return ids


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

        emoji_ids = _entity_custom_emoji_ids(reply)
        sticker = reply.get("sticker") or {}
        voice = reply.get("voice") or {}
        if emoji_ids:
            for eid in emoji_ids:
                lines.append(f"✨ Custom Emoji ID ➤ <code>{html.escape(eid)}</code>")
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
