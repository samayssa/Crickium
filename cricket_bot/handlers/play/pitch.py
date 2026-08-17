from __future__ import annotations

print("play/pitch.py loaded")

import asyncio

from handlers.registry import register_callback
from app import app
from database.play_repo import get_match, set_pitch, set_message_id
from utils.mentions import mention_html
from buttons.play_buttons import pitch_keyboard
from engines.play_engine import pitch_label

from .toss import send_toss_call

NO_KEYBOARD = {"inline_keyboard": []}


def _pitch_selection_text(challenger_mention: str) -> str:
    return (
        "<b>╭━━〔 🏟️ PITCH SELECT 〕━━╮\n\n"
        f"👤 {challenger_mention}\n\n"
        "The match is set. Now choose your battlefield. 🏏\n\n"
        "Select the pitch you want to play on.\n"
        "Your choice could shape the entire match. ⚡\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def send_pitch_selection(chat_id, match: dict) -> None:
    challenger_mention = mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])
    text = _pitch_selection_text(challenger_mention)
    keyboard = pitch_keyboard(match["match_id"])
    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
    await set_message_id(match["match_id"], sent["message_id"])


@register_callback("play_pitch")
async def on_play_pitch(callback_query):
    _, match_id_str, pitch_code = callback_query["data"].split(":")
    match_id = int(match_id_str)
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    match = await get_match(match_id)
    if not match or match["status"] != "accepted":
        await app.answer_callback_query(callback_query["id"], "Pitch selection is no longer active.", show_alert=True)
        return

    if int(presser["id"]) != int(match["challenger_id"]):
        await app.answer_callback_query(callback_query["id"], "Only the challenger selects the pitch!", show_alert=True)
        return

    await set_pitch(match_id, pitch_code)
    match["pitch"] = pitch_code
    challenger_mention = mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])

    await app.answer_callback_query(callback_query["id"], "Pitch locked!")
    await app.edit_message_text(
        chat_id, message_id,
        (
            f"<b>🏟️ {challenger_mention} selected {pitch_label(pitch_code)} Pitch.\n\n"
            "🔒 Pitch locked. Get ready for the toss! 🏏</b>"
        ),
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )

    await asyncio.sleep(1)
    await send_toss_call(chat_id, match)
