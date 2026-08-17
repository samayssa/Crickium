from __future__ import annotations

print("play/lineup.py loaded")

import asyncio

from handlers.registry import register_callback
from app import app
from database.play_repo import get_match, set_message_id
from engines.lineup_engine import load_current_xi
from utils.mentions import mention_html
from buttons.play_buttons import start_match_keyboard
from .live import begin_match_flow

NO_KEYBOARD = {"inline_keyboard": []}

# Guards against both players tapping "Start Match" at (almost) the
# same moment - without this, the second tap re-ran the whole start
# sequence (including a duplicate identical edit that Telegram
# rejects as MESSAGE_NOT_MODIFIED, crashing that callback) before
# engines.play_runtime even had a session registered to check against.
_STARTING: set[int] = set()


def _xi_block(xi: list[dict]) -> str:
    if not xi:
        return "No players in squad yet."
    return "\n".join(f"{i + 1}. {p.get('name', 'Unknown')}" for i, p in enumerate(xi))


def _playing_xi_text(match: dict, challenger_xi: list[dict], opponent_xi: list[dict]) -> str:
    challenger_mention = mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])
    opponent_mention = mention_html(match["opponent_id"], match["opponent_username"], match["opponent_name"])
    return (
        "<b>📋 PLAYING XI\n\n"
        f"🏏 {challenger_mention} XI</b>\n"
        f"<blockquote>{_xi_block(challenger_xi)}</blockquote>\n"
        "<b>\n              ⚔️ VS ⚔️\n\n"
        f"🎯 {opponent_mention} XI</b>\n"
        f"<blockquote>{_xi_block(opponent_xi)}</blockquote>"
    )


async def send_playing_xi(chat_id, match: dict) -> None:
    challenger_xi = await load_current_xi(match["challenger_id"]) or []
    opponent_xi = await load_current_xi(match["opponent_id"]) or []

    text = _playing_xi_text(match, challenger_xi, opponent_xi)
    keyboard = start_match_keyboard(match["match_id"])
    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
    await set_message_id(match["match_id"], sent["message_id"])


@register_callback("play_start")
async def on_play_start(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]

    match = await get_match(match_id)
    if not match:
        await app.answer_callback_query(callback_query["id"], "This match no longer exists.", show_alert=True)
        return

    if int(presser["id"]) not in (int(match["challenger_id"]), int(match["opponent_id"])):
        await app.answer_callback_query(
            callback_query["id"], "Only the two players in this match can start it!", show_alert=True,
        )
        return

    if match_id in _STARTING:
        await app.answer_callback_query(callback_query["id"], "The match is already starting!", show_alert=True)
        return
    _STARTING.add(match_id)

    await app.answer_callback_query(callback_query["id"], "🏁 Match starting soon!", show_alert=True)

    challenger_xi = await load_current_xi(match["challenger_id"]) or []
    opponent_xi = await load_current_xi(match["opponent_id"]) or []
    text = _playing_xi_text(match, challenger_xi, opponent_xi)

    await app.edit_message_text(
        callback_query["message"]["chat"]["id"],
        callback_query["message"]["message_id"],
        text,
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )

    asyncio.create_task(begin_match_flow(callback_query["message"]["chat"]["id"], match))
