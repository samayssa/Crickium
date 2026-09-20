from __future__ import annotations

import asyncio
import inspect
import json as _json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import aiohttp
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import API_HASH, API_ID, BOT_TOKEN

print(f"[app.py] Pyrogram adapter loaded. bot_token = {BOT_TOKEN[:8]}...{BOT_TOKEN[-4:]}")

# Some button fields (style, icon_custom_emoji_id - Bot API 9.4) only
# exist on maintained pyrogram forks like kurigram (see requirements.txt).
# Mainline pyrogram raises TypeError if you pass them. Check once at
# import time so this adapter keeps working either way.
_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)

# kurigram (MTProto) accepts "style" on InlineKeyboardButton without
# error, but doesn't actually forward it to Telegram over MTProto - the
# button comes back uncolored. The real HTTP Bot API DOES render it
# correctly (confirmed by testing a raw sendMessage call), so any
# reply_markup containing a "style" field gets routed through the plain
# HTTP Bot API instead of the pyrogram/kurigram client. Everything
# without a style keeps using the normal MTProto client, unchanged.
BOT_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _parse_mode(value: str | None):
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"markdown", "md"}:
        return ParseMode.MARKDOWN
    if normalized in {"markdownv2", "markdown_v2", "mdv2"}:
        return ParseMode.MARKDOWN_V2
    if normalized == "html":
        return ParseMode.HTML
    return value


def _raw_parse_mode(value: str | None) -> str | None:
    parsed = _parse_mode(value)
    if parsed is None:
        return None
    return getattr(parsed, "value", parsed)


def _wrap_user(user: Any | None) -> dict:
    if user is None:
        return {}
    return {
        "id": int(getattr(user, "id", 0) or 0),
        "is_bot": bool(getattr(user, "is_bot", False)),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
        "language_code": getattr(user, "language_code", None),
    }


def _wrap_chat(chat: Any | None) -> dict:
    if chat is None:
        return {}
    return {
        "id": int(getattr(chat, "id", 0) or 0),
        "type": str(getattr(chat, "type", "")),
        "title": getattr(chat, "title", None),
        "first_name": getattr(chat, "first_name", None),
        "username": getattr(chat, "username", None),
    }


def _wrap_photo(photo: Any | None) -> dict | None:
    if photo is None:
        return None
    return {
        "file_id": getattr(photo, "file_id", None),
        "file_unique_id": getattr(photo, "file_unique_id", None),
        "width": getattr(photo, "width", None),
        "height": getattr(photo, "height", None),
        "file_size": getattr(photo, "file_size", None),
    }


def _wrap_document(document: Any | None) -> dict | None:
    if document is None:
        return None
    return {
        "file_id": getattr(document, "file_id", None),
        "file_unique_id": getattr(document, "file_unique_id", None),
        "file_name": getattr(document, "file_name", None),
        "mime_type": getattr(document, "mime_type", None),
        "file_size": getattr(document, "file_size", None),
    }


def _wrap_message(message: Any | None) -> dict:
    if message is None:
        return {}
    wrapped = {
        "message_id": int(getattr(message, "id", 0) or 0),
        "date": int(getattr(message, "date", 0).timestamp()) if getattr(message, "date", None) else None,
        "chat": _wrap_chat(getattr(message, "chat", None)),
        "from": _wrap_user(getattr(message, "from_user", None)),
        "text": getattr(message, "text", None) or getattr(message, "caption", None) or "",
        "photo": _wrap_photo(getattr(message, "photo", None)),
        "document": _wrap_document(getattr(message, "document", None)),
        "entities": [],
    }
    reply = getattr(message, "reply_to_message", None)
    if reply is not None:
        wrapped["reply_to_message"] = _wrap_message(reply)
    return wrapped




def _contains_custom_emoji_markup(text: str | None) -> bool:
    return bool(text and "<tg-emoji" in str(text).lower())


def _strip_custom_emoji_markup(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", r"\1", str(text), flags=re.IGNORECASE | re.DOTALL)


def _strip_html_markup(text: str | None) -> str | None:
    """Best-effort plain-text fallback for Telegram entity parsing failures."""
    if text is None:
        return None
    cleaned = re.sub(r"<[^>]+>", "", str(text))
    # Keep common Telegram HTML entities readable in the fallback path.
    import html as _html
    return _html.unescape(cleaned)


def _is_custom_emoji_api_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "invalid custom emoji identifier" in text
        or "custom emoji identifier" in text
        or "custom emoji" in text and "invalid" in text
    )


def _is_entity_parse_api_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "can't parse entities" in text
        or "cant parse entities" in text
        or "parse entities" in text
        or "unsupported start tag" in text
        or "unsupported end tag" in text
        or "entity" in text and "bad request" in text
    )


def _is_bad_request_api_error(exc: Exception) -> bool:
    return "bad request" in str(exc).lower()


def _strip_custom_emoji_icons(markup: dict | None) -> dict | None:
    """Remove only invalid custom-emoji button icons, preserving button styles."""
    if not markup:
        return markup
    result = {"inline_keyboard": []}
    for row in markup.get("inline_keyboard", []):
        new_row = []
        for btn in row:
            if not isinstance(btn, dict):
                new_row.append(btn)
                continue
            copy = dict(btn)
            copy.pop("icon_custom_emoji_id", None)
            new_row.append(copy)
        result["inline_keyboard"].append(new_row)
    return result


def _contains_custom_emoji_icons(markup: dict | None) -> bool:
    if not markup:
        return False
    return any(
        bool(btn.get("icon_custom_emoji_id"))
        for row in markup.get("inline_keyboard", [])
        for btn in row
        if isinstance(btn, dict)
    )


def _strip_advanced_button_fields(markup: dict | None) -> dict | None:
    """Last-resort keyboard fallback for older/incompatible Bot API layers."""
    if not markup:
        return markup
    result = {"inline_keyboard": []}
    for row in markup.get("inline_keyboard", []):
        new_row = []
        for btn in row:
            if not isinstance(btn, dict):
                new_row.append(btn)
                continue
            copy = dict(btn)
            copy.pop("icon_custom_emoji_id", None)
            copy.pop("style", None)
            new_row.append(copy)
        result["inline_keyboard"].append(new_row)
    return result


def _wrap_http_message(result: dict) -> dict:
    """Same shape as _wrap_message(), but built from a raw HTTP Bot API
    JSON response instead of a pyrogram object."""
    chat = result.get("chat") or {}
    frm = result.get("from") or {}
    photo_sizes = result.get("photo") or []
    photo = None
    if photo_sizes:
        largest = photo_sizes[-1]
        photo = {
            "file_id": largest.get("file_id"),
            "file_unique_id": largest.get("file_unique_id"),
            "width": largest.get("width"),
            "height": largest.get("height"),
            "file_size": largest.get("file_size"),
        }
    return {
        "message_id": result.get("message_id"),
        "date": result.get("date"),
        "chat": {
            "id": chat.get("id"),
            "type": chat.get("type"),
            "title": chat.get("title"),
            "first_name": chat.get("first_name"),
            "username": chat.get("username"),
        },
        "from": {
            "id": frm.get("id"),
            "is_bot": frm.get("is_bot"),
            "first_name": frm.get("first_name"),
            "last_name": frm.get("last_name"),
            "username": frm.get("username"),
            "language_code": frm.get("language_code"),
        },
        "text": result.get("text") or result.get("caption") or "",
        "photo": photo,
        "entities": [],
    }


def _button_to_json(btn: Any) -> dict:
    if isinstance(btn, dict):
        return {k: v for k, v in btn.items() if v is not None}

    entry: dict[str, Any] = {"text": getattr(btn, "text", "Button")}
    callback_data = getattr(btn, "callback_data", None)
    if callback_data:
        entry["callback_data"] = callback_data.decode() if isinstance(callback_data, bytes) else callback_data
    url = getattr(btn, "url", None)
    if url:
        entry["url"] = url
    style = getattr(btn, "style", None)
    if style:
        entry["style"] = style
    icon = getattr(btn, "icon_custom_emoji_id", None)
    if icon:
        entry["icon_custom_emoji_id"] = icon
    return entry


def _reply_markup_to_json(reply_markup: Any | None) -> dict | None:
    if reply_markup is None:
        return None

    if isinstance(reply_markup, dict):
        rows = reply_markup.get("inline_keyboard", [])
    else:
        rows = getattr(reply_markup, "inline_keyboard", [])

    if not rows:
        return None

    return {"inline_keyboard": [[_button_to_json(btn) for btn in row] for row in rows]}


def _markup_has_style(markup_json: dict | None) -> bool:
    if not markup_json:
        return False
    return any(
        btn.get("style") or btn.get("web_app") or btn.get("icon_custom_emoji_id")
        for row in markup_json.get("inline_keyboard", [])
        for btn in row
    )


def _convert_reply_markup(reply_markup: Any | None):
    if reply_markup is None:
        return None

    if isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup

    if isinstance(reply_markup, dict):
        rows = []

        for row in reply_markup.get("inline_keyboard", []):
            buttons = []

            for btn in row:
                if isinstance(btn, InlineKeyboardButton):
                    buttons.append(btn)
                    continue

                if not isinstance(btn, dict):
                    continue

                button_kwargs = {
                    "text": str(btn.get("text", "Button")),
                    "callback_data": btn.get("callback_data"),
                    "url": btn.get("url"),
                }
                if btn.get("style") is not None and "style" in _BUTTON_PARAMS:
                    button_kwargs["style"] = btn.get("style")
                if btn.get("icon_custom_emoji_id") is not None and "icon_custom_emoji_id" in _BUTTON_PARAMS:
                    button_kwargs["icon_custom_emoji_id"] = btn.get("icon_custom_emoji_id")

                buttons.append(InlineKeyboardButton(**button_kwargs))

            if buttons:
                rows.append(buttons)

        if rows:
            return InlineKeyboardMarkup(rows)

    return None


_RETRY_AFTER_RE = re.compile(r"retry after (\d+)")


async def _resilient(coro_factory, *, max_retries: int = 4, label: str = ""):
    """Calls coro_factory() (a zero-arg callable that returns a FRESH
    coroutine on every call - needed since a coroutine can only be
    awaited once) with automatic retry on Telegram flood waits, and a
    silent no-op on "message not modified". This is what stops a
    single rate-limit hit from killing an in-progress /play over -
    every outgoing Telegram call in this file goes through here."""
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except FloodWait as exc:
            attempt += 1
            wait_for = int(getattr(exc, "value", None) or getattr(exc, "x", 3) or 3)
            if attempt > max_retries:
                print(f"[app.py] {label}: giving up after {max_retries} FloodWait retries.")
                raise
            print(f"[app.py] {label}: FloodWait {wait_for}s (attempt {attempt}/{max_retries}) - retrying...")
            await asyncio.sleep(wait_for + 1)
        except MessageNotModified:
            return {}
        except RuntimeError as exc:
            text = str(exc).lower()
            if "message is not modified" in text:
                return {}
            if "retry after" in text or "429" in text or "too many requests" in text:
                attempt += 1
                match = _RETRY_AFTER_RE.search(text)
                retry_after = int(match.group(1)) if match else 3
                if attempt > max_retries:
                    print(f"[app.py] {label}: giving up after {max_retries} HTTP 429 retries.")
                    raise
                print(f"[app.py] {label}: HTTP 429, retrying after {retry_after}s (attempt {attempt}/{max_retries})...")
                await asyncio.sleep(retry_after + 1)
                continue
            raise


class App:
    def __init__(self):
        self.client: Client | None = None

    def bind_client(self, client: Client):
        self.client = client

    @property
    def _client(self) -> Client:
        if self.client is None:
            raise RuntimeError("Pyrogram client has not been bound yet.")
        return self.client

    async def _call_bot_api(self, method: str, *, data=None, json_payload=None) -> dict:
        url = f"{BOT_API_BASE}/{method}"
        async with aiohttp.ClientSession() as session:
            if json_payload is not None:
                async with session.post(url, json=json_payload) as resp:
                    result = await resp.json()
            else:
                async with session.post(url, data=data) as resp:
                    result = await resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram HTTP API error on {method}: {result}")
        return result["result"]

    async def get_me(self):
        me = await self._client.get_me()
        return _wrap_user(me)

    async def get_updates(self, offset=None, timeout=25, allowed_updates=None):
        # Pyrogram is event-driven. This method stays only for backward compatibility.
        return []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, disable_web_page_preview=False):
        print(f"[app.py] send_message -> chat_id={chat_id} text={text!r}")

        markup_json = _reply_markup_to_json(reply_markup)
        if _markup_has_style(markup_json) or _contains_custom_emoji_markup(text):
            print("[app.py] send_message -> routing through raw HTTP Bot API")
            payload = {"chat_id": chat_id, "text": text}
            if disable_web_page_preview:
                payload["link_preview_options"] = {"is_disabled": True}
            if markup_json is not None:
                payload["reply_markup"] = markup_json
            raw_mode = _raw_parse_mode(parse_mode)
            if raw_mode:
                payload["parse_mode"] = raw_mode
            try:
                result = await _resilient(
                    lambda: self._call_bot_api("sendMessage", json_payload=payload), label="send_message(http)",
                )
            except Exception as exc:
                # If a configured custom-emoji ID has expired/been revoked,
                # preserve the button styles and retry without only that icon.
                if _is_custom_emoji_api_error(exc) and _contains_custom_emoji_icons(markup_json):
                    print(f"[app.py] Invalid button custom emoji; retrying without icon IDs: {exc!r}")
                    payload["reply_markup"] = _strip_custom_emoji_icons(markup_json)
                    try:
                        result = await _resilient(
                            lambda: self._call_bot_api("sendMessage", json_payload=payload),
                            label="send_message(icon-fallback)",
                        )
                    except Exception as retry_exc:
                        # Some deployments expose a Bot API layer that also
                        # rejects the newer button style field. As a final
                        # keyboard-only fallback, keep the same buttons/data
                        # but remove style/icon metadata.
                        if markup_json:
                            print(f"[app.py] Advanced button fields rejected; retrying basic keyboard: {retry_exc!r}")
                            payload["reply_markup"] = _strip_advanced_button_fields(markup_json)
                            try:
                                result = await _resilient(
                                    lambda: self._call_bot_api("sendMessage", json_payload=payload),
                                    label="send_message(basic-keyboard-fallback)",
                                )
                            except Exception as keyboard_exc:
                                if _contains_custom_emoji_markup(text):
                                    print(f"[app.py] Custom emoji text send failed; using Unicode fallback: {keyboard_exc!r}")
                                    payload["text"] = _strip_custom_emoji_markup(text)
                                    result = await _resilient(
                                        lambda: self._call_bot_api("sendMessage", json_payload=payload),
                                        label="send_message(fallback)",
                                    )
                                elif _is_entity_parse_api_error(keyboard_exc) and parse_mode:
                                    print(f"[app.py] HTML entity parsing failed after keyboard fallback; using plain text: {keyboard_exc!r}")
                                    payload.pop("parse_mode", None)
                                    payload["text"] = _strip_html_markup(text)
                                    result = await _resilient(
                                        lambda: self._call_bot_api("sendMessage", json_payload=payload),
                                        label="send_message(plain-fallback)",
                                    )
                                else:
                                    raise
                        elif _contains_custom_emoji_markup(text):
                            print(f"[app.py] Custom emoji text send failed; using Unicode fallback: {retry_exc!r}")
                            payload["text"] = _strip_custom_emoji_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("sendMessage", json_payload=payload),
                                label="send_message(fallback)",
                            )
                        elif _is_entity_parse_api_error(retry_exc) and parse_mode:
                            print(f"[app.py] HTML entity parsing failed after icon fallback; using plain text: {retry_exc!r}")
                            payload.pop("parse_mode", None)
                            payload["text"] = _strip_html_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("sendMessage", json_payload=payload),
                                label="send_message(plain-fallback)",
                            )
                        else:
                            raise
                elif _is_bad_request_api_error(exc) and markup_json:
                    print(f"[app.py] Telegram rejected the advanced keyboard; retrying with a basic keyboard: {exc!r}")
                    payload["reply_markup"] = _strip_advanced_button_fields(markup_json)
                    try:
                        result = await _resilient(
                            lambda: self._call_bot_api("sendMessage", json_payload=payload),
                            label="send_message(basic-keyboard-fallback)",
                        )
                    except Exception as retry_exc:
                        if _contains_custom_emoji_markup(text):
                            print(f"[app.py] Custom emoji text send failed; using Unicode fallback: {retry_exc!r}")
                            payload["text"] = _strip_custom_emoji_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("sendMessage", json_payload=payload), label="send_message(fallback)",
                            )
                        elif _is_entity_parse_api_error(retry_exc) and parse_mode:
                            print(f"[app.py] HTML entity parsing failed after keyboard fallback; using plain text: {retry_exc!r}")
                            payload.pop("parse_mode", None)
                            payload["text"] = _strip_html_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("sendMessage", json_payload=payload), label="send_message(plain-fallback)",
                            )
                        else:
                            raise
                elif _contains_custom_emoji_markup(text):
                    print(f"[app.py] Custom emoji send failed; using Unicode fallback: {exc!r}")
                    payload["text"] = _strip_custom_emoji_markup(text)
                    result = await _resilient(
                        lambda: self._call_bot_api("sendMessage", json_payload=payload), label="send_message(fallback)",
                    )
                elif _is_entity_parse_api_error(exc) and parse_mode:
                    print(f"[app.py] HTML entity parsing failed; using plain text fallback: {exc!r}")
                    payload.pop("parse_mode", None)
                    payload["text"] = _strip_html_markup(text)
                    result = await _resilient(
                        lambda: self._call_bot_api("sendMessage", json_payload=payload), label="send_message(plain-fallback)",
                    )
                else:
                    raise
            return _wrap_http_message(result)

        msg = await _resilient(
            lambda: self._client.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=_parse_mode(parse_mode),
                reply_markup=_convert_reply_markup(reply_markup),
                **({"link_preview_options": {"is_disabled": True}} if disable_web_page_preview else {}),
            ),
            label="send_message",
        )
        return _wrap_message(msg)

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        print(f"[app.py] edit_message_text -> chat_id={chat_id} message_id={message_id} text={text!r}")

        markup_json = _reply_markup_to_json(reply_markup)
        if _markup_has_style(markup_json) or _contains_custom_emoji_markup(text):
            print("[app.py] edit_message_text -> routing through raw HTTP Bot API")
            payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
            if markup_json is not None:
                payload["reply_markup"] = markup_json
            raw_mode = _raw_parse_mode(parse_mode)
            if raw_mode:
                payload["parse_mode"] = raw_mode
            try:
                result = await _resilient(
                    lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(http)",
                )
            except Exception as exc:
                if _is_custom_emoji_api_error(exc) and _contains_custom_emoji_icons(markup_json):
                    print(f"[app.py] Invalid button custom emoji on edit; retrying without icon IDs: {exc!r}")
                    payload["reply_markup"] = _strip_custom_emoji_icons(markup_json)
                    try:
                        result = await _resilient(
                            lambda: self._call_bot_api("editMessageText", json_payload=payload),
                            label="edit_message_text(icon-fallback)",
                        )
                    except Exception as retry_exc:
                        if markup_json:
                            print(f"[app.py] Advanced button fields rejected on edit; retrying basic keyboard: {retry_exc!r}")
                            payload["reply_markup"] = _strip_advanced_button_fields(markup_json)
                            try:
                                result = await _resilient(
                                    lambda: self._call_bot_api("editMessageText", json_payload=payload),
                                    label="edit_message_text(basic-keyboard-fallback)",
                                )
                            except Exception as keyboard_exc:
                                if _contains_custom_emoji_markup(text):
                                    print(f"[app.py] Custom emoji edit failed; using Unicode fallback: {keyboard_exc!r}")
                                    payload["text"] = _strip_custom_emoji_markup(text)
                                    result = await _resilient(
                                        lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(fallback)",
                                    )
                                elif _is_entity_parse_api_error(keyboard_exc) and parse_mode:
                                    print(f"[app.py] HTML entity parsing failed after keyboard fallback; using plain text: {keyboard_exc!r}")
                                    payload.pop("parse_mode", None)
                                    payload["text"] = _strip_html_markup(text)
                                    result = await _resilient(
                                        lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(plain-fallback)",
                                    )
                                else:
                                    raise
                        elif _contains_custom_emoji_markup(text):
                            print(f"[app.py] Custom emoji edit failed; using Unicode fallback: {retry_exc!r}")
                            payload["text"] = _strip_custom_emoji_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(fallback)",
                            )
                        elif _is_entity_parse_api_error(retry_exc) and parse_mode:
                            print(f"[app.py] HTML entity parsing failed after icon fallback; using plain text: {retry_exc!r}")
                            payload.pop("parse_mode", None)
                            payload["text"] = _strip_html_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(plain-fallback)",
                            )
                        else:
                            raise
                elif _is_bad_request_api_error(exc) and markup_json:
                    print(f"[app.py] Telegram rejected the advanced edit keyboard; retrying with a basic keyboard: {exc!r}")
                    payload["reply_markup"] = _strip_advanced_button_fields(markup_json)
                    try:
                        result = await _resilient(
                            lambda: self._call_bot_api("editMessageText", json_payload=payload),
                            label="edit_message_text(basic-keyboard-fallback)",
                        )
                    except Exception as retry_exc:
                        if _contains_custom_emoji_markup(text):
                            print(f"[app.py] Custom emoji edit failed; using Unicode fallback: {retry_exc!r}")
                            payload["text"] = _strip_custom_emoji_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(fallback)",
                            )
                        elif _is_entity_parse_api_error(retry_exc) and parse_mode:
                            print(f"[app.py] HTML entity parsing failed after keyboard fallback; using plain text: {retry_exc!r}")
                            payload.pop("parse_mode", None)
                            payload["text"] = _strip_html_markup(text)
                            result = await _resilient(
                                lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(plain-fallback)",
                            )
                        else:
                            raise
                elif _contains_custom_emoji_markup(text):
                    print(f"[app.py] Custom emoji edit failed; using Unicode fallback: {exc!r}")
                    payload["text"] = _strip_custom_emoji_markup(text)
                    result = await _resilient(
                        lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(fallback)",
                    )
                elif _is_entity_parse_api_error(exc) and parse_mode:
                    print(f"[app.py] HTML entity parsing failed; using plain text fallback: {exc!r}")
                    payload.pop("parse_mode", None)
                    payload["text"] = _strip_html_markup(text)
                    result = await _resilient(
                        lambda: self._call_bot_api("editMessageText", json_payload=payload), label="edit_message_text(plain-fallback)",
                    )
                else:
                    raise
            return _wrap_http_message(result)

        msg = await _resilient(
            lambda: self._client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=_parse_mode(parse_mode),
                reply_markup=_convert_reply_markup(reply_markup),
            ),
            label="edit_message_text",
        )
        return _wrap_message(msg) if msg else {}

    async def edit_message_media(self, chat_id, message_id, photo, caption=None, parse_mode=None, reply_markup=None):
        """Edit an existing photo message in place, preserving its message id."""
        print(f"[app.py] edit_message_media -> chat_id={chat_id} message_id={message_id}")
        markup_json = _reply_markup_to_json(reply_markup)
        raw_mode = _raw_parse_mode(parse_mode)
        if _markup_has_style(markup_json) or isinstance(photo, (bytes, bytearray)) or hasattr(photo, "read"):
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("message_id", str(message_id))
            media = {"type": "photo", "media": "attach://edited_photo"}
            if caption is not None:
                media["caption"] = caption
                if raw_mode:
                    media["parse_mode"] = raw_mode
            form.add_field("media", _json.dumps(media))
            if reply_markup is not None:
                if markup_json is not None:
                    form.add_field("reply_markup", _json.dumps(markup_json))
            if isinstance(photo, (bytes, bytearray)):
                form.add_field("edited_photo", bytes(photo), filename="image.png", content_type="image/png")
            else:
                photo.seek(0)
                form.add_field("edited_photo", photo.read(), filename="image.png", content_type="image/png")
            result = await _resilient(
                lambda: self._call_bot_api("editMessageMedia", data=form), label="edit_message_media(http)",
            )
            return _wrap_http_message(result)

        from pyrogram.types import InputMediaPhoto
        media = InputMediaPhoto(media=photo, caption=caption, parse_mode=_parse_mode(parse_mode))
        msg = await _resilient(
            lambda: self._client.edit_message_media(
                chat_id=chat_id, message_id=message_id, media=media, reply_markup=_convert_reply_markup(reply_markup)
            ), label="edit_message_media",
        )
        return _wrap_message(msg) if msg else {}

    async def delete_message(self, chat_id, message_id):
        print(f"[app.py] delete_message -> chat_id={chat_id} message_id={message_id}")
        try:
            await _resilient(lambda: self._client.delete_messages(chat_id, message_id), label="delete_message")
            return {}
        except Exception as exc:
            msg = str(exc).lower()
            if "message to delete not found" in msg:
                print(f"[app.py] Non-fatal delete_message error ignored: {exc!r}")
                return {}
            raise

    async def _send_media_file(self, method: str, field_name: str, chat_id, media, caption=None, parse_mode=None):
        """Send animation/video from a file_id, URL, bytes, file object or path."""
        print(f"[app.py] {method} -> chat_id={chat_id} caption={caption!r}")

        raw_mode = _raw_parse_mode(parse_mode)
        use_multipart = isinstance(media, (bytes, bytearray)) or hasattr(media, "read") or (isinstance(media, str) and Path(media).is_file())

        if use_multipart:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            if caption:
                form.add_field("caption", caption)
            if raw_mode:
                form.add_field("parse_mode", raw_mode)

            if isinstance(media, (bytes, bytearray)):
                filename = "media.bin"
                content_type = "application/octet-stream"
                form.add_field(field_name, bytes(media), filename=filename, content_type=content_type)
            elif hasattr(media, "read"):
                media.seek(0)
                form.add_field(field_name, media.read(), filename=getattr(media, "name", "media.bin"), content_type="application/octet-stream")
            else:
                path = Path(str(media))
                with path.open("rb") as fh:
                    form.add_field(field_name, fh.read(), filename=path.name, content_type="application/octet-stream")

            result = await _resilient(
                lambda: self._call_bot_api(method, data=form),
                label=f"{method}(http)",
            )
            return _wrap_http_message(result)

        payload = {"chat_id": chat_id, field_name: str(media)}
        if caption:
            payload["caption"] = caption
        if raw_mode:
            payload["parse_mode"] = raw_mode
        try:
            result = await _resilient(
                lambda: self._call_bot_api(method, json_payload=payload),
                label=f"{method}(http)",
            )
            return _wrap_http_message(result)
        except Exception as exc:
            # Custom emoji in a caption can fail when an ID was revoked. Keep
            # the media delivery alive with the Unicode-only caption.
            if _contains_custom_emoji_markup(caption):
                payload["caption"] = _strip_custom_emoji_markup(caption)
                result = await _resilient(
                    lambda: self._call_bot_api(method, json_payload=payload),
                    label=f"{method}(unicode-fallback)",
                )
                return _wrap_http_message(result)
            raise

    async def send_animation(self, chat_id, animation, caption=None, parse_mode=None):
        return await self._send_media_file(
            "sendAnimation", "animation", chat_id, animation, caption=caption, parse_mode=parse_mode
        )

    async def send_video(self, chat_id, video, caption=None, parse_mode=None):
        return await self._send_media_file(
            "sendVideo", "video", chat_id, video, caption=caption, parse_mode=parse_mode
        )

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        print(f"[app.py] send_photo -> chat_id={chat_id} caption={caption!r}")

        markup_json = _reply_markup_to_json(reply_markup)
        if _markup_has_style(markup_json) or _contains_custom_emoji_markup(caption):
            print("[app.py] send_photo -> routing through raw HTTP Bot API")
            async def _send_photo_http(caption_value):
                form = aiohttp.FormData()
                form.add_field("chat_id", str(chat_id))
                if caption_value:
                    form.add_field("caption", caption_value)
                raw_mode = _raw_parse_mode(parse_mode)
                if raw_mode:
                    form.add_field("parse_mode", raw_mode)
                if markup_json is not None:
                    form.add_field("reply_markup", _json.dumps(markup_json))
                if isinstance(photo, (bytes, bytearray)):
                    form.add_field("photo", bytes(photo), filename="image.png", content_type="image/png")
                elif hasattr(photo, "read"):
                    photo.seek(0)
                    form.add_field("photo", photo.read(), filename="image.png", content_type="image/png")
                else:
                    form.add_field("photo", str(photo))
                return await self._call_bot_api("sendPhoto", data=form)
            try:
                result = await _resilient(lambda: _send_photo_http(caption), label="send_photo(http)")
            except Exception as exc:
                if _contains_custom_emoji_markup(caption):
                    print(f"[app.py] Custom emoji photo caption failed; using Unicode fallback: {exc!r}")
                    result = await _resilient(lambda: _send_photo_http(_strip_custom_emoji_markup(caption)), label="send_photo(fallback)")
                else:
                    raise
            return _wrap_http_message(result)

        if isinstance(photo, (bytes, bytearray)):
            buffer = BytesIO(photo)
            buffer.name = "image.png"
            photo = buffer
        msg = await _resilient(
            lambda: self._client.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode=_parse_mode(parse_mode),
                reply_markup=_convert_reply_markup(reply_markup),
            ),
            label="send_photo",
        )
        return _wrap_message(msg)

    async def forward_message(self, target_chat_id, from_chat_id, message_id):
        print(f"[app.py] forward_message -> target_chat_id={target_chat_id} from_chat_id={from_chat_id} message_id={message_id}")
        msg = await _resilient(
            lambda: self._client.forward_messages(chat_id=target_chat_id, from_chat_id=from_chat_id, message_ids=message_id),
            label="forward_message",
        )
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        return _wrap_message(msg) if msg else {}

    async def send_document(self, chat_id, document, filename=None, caption=None, parse_mode=None):
        print(f"[app.py] send_document -> chat_id={chat_id} filename={filename!r}")

        if isinstance(document, (bytes, bytearray)):
            buffer = BytesIO(document)
            buffer.name = filename or "backup.json.gz"
            document = buffer
        elif hasattr(document, "read") and filename:
            document.name = filename

        msg = await _resilient(
            lambda: self._client.send_document(
                chat_id=chat_id,
                document=document,
                caption=caption,
                parse_mode=_parse_mode(parse_mode),
            ),
            label="send_document",
        )
        return _wrap_message(msg)

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode=None, reply_markup=None):
        print(f"[app.py] edit_message_caption -> chat_id={chat_id} message_id={message_id}")

        markup_json = _reply_markup_to_json(reply_markup)
        if _markup_has_style(markup_json) or _contains_custom_emoji_markup(caption):
            print("[app.py] edit_message_caption -> routing through raw HTTP Bot API")
            async def _edit_caption_http(caption_value):
                form = aiohttp.FormData()
                form.add_field("chat_id", str(chat_id))
                form.add_field("message_id", str(message_id))
                form.add_field("caption", caption_value)
                raw_mode = _raw_parse_mode(parse_mode)
                if raw_mode:
                    form.add_field("parse_mode", raw_mode)
                if markup_json is not None:
                    form.add_field("reply_markup", _json.dumps(markup_json))
                return await self._call_bot_api("editMessageCaption", data=form)
            try:
                result = await _resilient(lambda: _edit_caption_http(caption), label="edit_message_caption(http)")
            except Exception as exc:
                if _contains_custom_emoji_markup(caption):
                    print(f"[app.py] Custom emoji caption edit failed; using Unicode fallback: {exc!r}")
                    result = await _resilient(lambda: _edit_caption_http(_strip_custom_emoji_markup(caption)), label="edit_message_caption(fallback)")
                else:
                    raise
            return _wrap_http_message(result)

        msg = await _resilient(
            lambda: self._client.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=caption,
                parse_mode=_parse_mode(parse_mode),
                reply_markup=_convert_reply_markup(reply_markup),
            ),
            label="edit_message_caption",
        )
        return _wrap_message(msg) if msg else {}

    async def pin_chat_message(self, chat_id, message_id, disable_notification: bool = True):
        print(f"[app.py] pin_chat_message -> chat_id={chat_id} message_id={message_id}")
        payload = {"chat_id": chat_id, "message_id": message_id, "disable_notification": disable_notification}
        try:
            return await _resilient(
                lambda: self._call_bot_api("pinChatMessage", json_payload=payload), label="pin_chat_message",
            )
        except Exception as exc:
            print(f"[app.py] Non-fatal pin_chat_message error ignored: {exc!r}")
            return {}

    async def download_media(self, file_id: str) -> bytes:
        """Downloads any file (e.g. a photo) fully into memory and returns its raw bytes."""
        print(f"[app.py] download_media -> file_id={file_id[:20]}...")
        buffer = await self._client.download_media(file_id, in_memory=True)
        buffer.seek(0)
        return buffer.read()

    async def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        print(f"[app.py] answer_callback_query -> id={callback_query_id} text={text!r}")
        try:
            await self._client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=text,
                show_alert=show_alert,
            )
            return {}
        except Exception as exc:
            msg = str(exc).lower()
            if "query is too old" in msg or "query id is invalid" in msg:
                print(f"[app.py] Non-fatal answer_callback_query error ignored: {exc!r}")
                return {}
            print(f"[app.py] Non-fatal answer_callback_query error ignored: {exc!r}")
            return {}


app = App()
print("[app.py] App object created.")
