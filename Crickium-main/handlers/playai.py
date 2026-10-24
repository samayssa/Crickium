print("playai.py loaded")

import html
import os
import re

from handlers.registry import register
from app import app
from database import miniapp_game_repo

_OVERS = (2, 5, 10, 20)


def _bot_username() -> str:
    username = os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot").strip().lstrip("@")
    return username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else "CrickiumBot"


def _overs_from(text: str) -> int | None:
    """`/playai` -> None (choose in the app); `/playai 5` or `/playai@Bot 5` -> 5 (only 2, 5, 10, 20)."""
    rest = (text or "").strip().partition(" ")[2]
    match = re.match(r"\s*(\d{1,3})\b", rest)
    if not match:
        return None
    value = int(match.group(1))
    return value if value in _OVERS else None


@register("playai")
async def playai_command(message):
    chat = message["chat"]
    user = message.get("from") or {}
    user_id = int(user.get("id") or 0)
    if not user_id:
        return
    first_name = user.get("first_name") or "Player"
    text = message.get("text") or ""
    overs = _overs_from(text)
    bad_number = False
    if overs is None:
        m = re.search(r"\b(\d{1,3})\b", text.partition(" ")[2])
        bad_number = bool(m)

    token = await miniapp_game_repo.create_link(user_id, first_name, int(chat["id"]), message.get("message_id"), overs)
    url = f"https://t.me/{_bot_username()}?startapp=pai_{token}&mode=fullscreen"
    mention = f'<a href="tg://user?id={user_id}">{html.escape(first_name)}</a>'
    lines = [
        "<b>🎮 PLAY AI GAME STARTED</b>",
        "",
        f"<blockquote><b>👤 Player ➤ {mention}</b>\n"
        f"<b>🤖 Opponent ➤ Crickium AI</b>\n"
        f"<b>🏏 Overs ➤ {overs if overs else 'choose in the app'}</b></blockquote>",
        "",
        "<b>Go to the Mini App to play. 🚀</b>",
        "<i>Anyone can tap the button to watch the match live.</i>",
    ]
    if bad_number:
        lines.insert(2, "<i>Overs must be 2, 5, 10 or 20 - pick it in the app.</i>")
    markup = {"inline_keyboard": [[{"text": "🎮 Open Mini App", "url": url, "style": "primary"}]]}
    await app.send_message(chat["id"], "\n".join(lines), parse_mode="HTML", reply_markup=markup)
