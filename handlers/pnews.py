"""Admin-only publishing command for Mini App news articles.

Usage: reply to a photo whose caption is formatted as:

    A concise news title

    The full article description goes here.

The stored Telegram file_id remains private to the server. The Mini App serves
images through its authenticated image proxy instead of exposing bot credentials.
"""
from __future__ import annotations

import html

from config import ADMIN_USER_ID
from database.query import fetchval
from handlers.registry import register
from services.news_articles import parse_news_caption
from app import app



def _image_file_id(reply: dict) -> str | None:
    photo = reply.get("photo")
    if isinstance(photo, list):
        photo = max(
            (item for item in photo if isinstance(item, dict)),
            key=lambda item: int(item.get("file_size") or 0),
            default=None,
        )
    if isinstance(photo, dict) and photo.get("file_id"):
        return str(photo["file_id"])
    document = reply.get("document")
    if isinstance(document, dict) and str(document.get("mime_type") or "").lower().startswith("image/") and document.get("file_id"):
        return str(document["file_id"])
    return None


@register("pnews")
async def pnews_command(message: dict) -> None:
    sender_id = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    configured_admin = int(ADMIN_USER_ID or 0)
    if not configured_admin or sender_id != configured_admin:
        await app.send_message(chat_id, "⛔ This command is available to the Crickium admin only.")
        return

    reply = message.get("reply_to_message") or {}
    if not isinstance(reply, dict):
        await app.send_message(chat_id, "Reply to a news image with a caption, then send /pnews.")
        return
    file_id = _image_file_id(reply)
    caption = str(reply.get("text") or "").strip()
    parsed = parse_news_caption(caption)
    if not file_id or not parsed:
        await app.send_message(
            chat_id,
            "⚠️ News was not published. Reply to an image with this caption format:\n\n"
            "News title\n\nFull news description goes here.\n\n"
            "Then send /pnews as a reply to that image.",
        )
        return

    title, description = parsed
    try:
        article_id = await fetchval(
            """
            INSERT INTO miniapp_news_articles(title,description,image_file_id,created_by,is_published)
            VALUES($1,$2,$3,$4,TRUE)
            RETURNING article_id;
            """,
            title,
            description,
            file_id,
            sender_id,
        )
    except Exception as exc:
        print(f"[pnews] publication failed: {type(exc).__name__}")
        await app.send_message(chat_id, "⚠️ News could not be saved. Check the bot database connection and try again.")
        return

    await app.send_message(
        chat_id,
        f"✅ <b>News published</b>\n\n<b>Article:</b> {int(article_id or 0)}\n<b>Title:</b> "
        f"{html.escape(title)}\n\nThe article is now available in the Mini App’s News Update section.",
        parse_mode="HTML",
    )
