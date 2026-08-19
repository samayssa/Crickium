from __future__ import annotations

from handlers.registry import register
from app import app
from config import NOTIFICATION_GROUP_ID


async def _forward_report(message, command_name: str):
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    text = str(message.get("text") or "").strip()

    # Require actual text after the command.
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await app.answer_message(
            message,
            f"Please use /{command_name} followed by your message.",
        )
        return

    try:
        await app.forward_message(
            target_chat_id=int(NOTIFICATION_GROUP_ID),
            from_chat_id=chat_id,
            message_id=message_id,
        )
    except Exception as exc:
        print(f"[{command_name}] forward failed: {exc!r}")
        await app.answer_message(
            message,
            "⚠️ Failed to send your message. Please try again.",
        )


@register("feedback")
async def feedback_command(message):
    await _forward_report(message, "feedback")
