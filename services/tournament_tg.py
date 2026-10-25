"""The one place the tournament code talks to Telegram.

Everything that sends, edits, pins or deletes a message for a tournament (the auction presenter, the match-day
worker, result cards) goes through the adapter returned by :func:`get`. Tests swap it for a fake with
:func:`set_telegram`; production uses :class:`AppTelegram`, which wraps the bot's own ``app`` object (so
FloodWait handling and the styled-button routing of ``app.py`` keep working) and plain Bot API calls for
pin / unpin (so a missing "pin messages" right is reported instead of swallowed).
"""
from __future__ import annotations

from typing import Any


class AppTelegram:
    async def send(self, chat_id: int, text: str, markup: dict | None = None) -> int | None:
        from app import app
        msg = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        return int((msg or {}).get("message_id") or 0) or None

    async def send_rich(self, chat_id: int, html_text: str, markup: dict | None = None) -> bool:
        """Rich Message (headings, lists). False when Telegram does not accept it - the caller then sends a normal message."""
        from services.giveaway import tg_call
        try:
            payload = {"chat_id": chat_id, "rich_message": {"html": html_text}}
            if markup:
                payload["reply_markup"] = markup
            res = await tg_call("sendRichMessage", payload)
            return bool(isinstance(res, dict) and res.get("message_id"))
        except Exception as exc:
            print(f"[tournament/tg] rich message refused: {exc!r}")
            return False

    async def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None = None) -> bool:
        from app import app
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup)
        return True

    async def delete(self, chat_id: int, message_id: int) -> bool:
        from app import app
        await app.delete_message(chat_id, message_id)
        return True

    async def pin(self, chat_id: int, message_id: int) -> None:
        from services.giveaway import tg_call
        await tg_call("pinChatMessage", {"chat_id": chat_id, "message_id": message_id, "disable_notification": True})

    async def unpin(self, chat_id: int, message_id: int) -> None:
        from services.giveaway import tg_call
        await tg_call("unpinChatMessage", {"chat_id": chat_id, "message_id": message_id})

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        """True when ``user_id`` is an administrator / owner of the chat (False on any error)."""
        from services.giveaway import tg_call
        try:
            member = await tg_call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        except Exception:
            return False
        return str((member or {}).get("status") or "") in {"creator", "administrator"}


_TELEGRAM: Any = AppTelegram()


def get() -> Any:
    return _TELEGRAM


def set_telegram(adapter: Any) -> None:
    global _TELEGRAM
    _TELEGRAM = adapter
