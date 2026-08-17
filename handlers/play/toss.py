from __future__ import annotations

print("play/toss.py loaded")

import asyncio

from handlers.registry import register_callback
from app import app
from database.play_repo import get_match, set_message_id, set_toss, set_decision
from utils.mentions import mention_html
from buttons.play_buttons import toss_call_keyboard, bat_bowl_keyboard
from engines.play_engine import flip_coin

from .lineup import send_playing_xi

NO_KEYBOARD = {"inline_keyboard": []}

_FRAME_1 = "🪙 Tossing the coin...\n       ↻  ◌  ↺"
_FRAME_2 = "🪙 Coin spinning high...\n       ◐  ◓  ◑"
_FRAME_3 = "🪙 And it's coming down...\n       ◒  ◉  ◐"

_CALL_LABEL = {"heads": "🗿 HEADS", "tails": "🦅 TAILS"}


def _toss_call_text(opponent_mention: str) -> str:
    return (
        "<b>╭━━〔 🪙 TOSS CALL 〕━━╮\n\n"
        f"👤 {opponent_mention}\n\n"
        "The toss is yours to call. 🏏\n"
        "Choose your side of the coin.\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def send_toss_call(chat_id, match: dict) -> None:
    opponent_mention = mention_html(match["opponent_id"], match["opponent_username"], match["opponent_name"])
    text = _toss_call_text(opponent_mention)
    keyboard = toss_call_keyboard(match["match_id"])
    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
    await set_message_id(match["match_id"], sent["message_id"])


def _toss_result_text(winner_mention: str, call: str, result: str) -> str:
    return (
        "<b>╭━━〔 🪙 TOSS RESULT 〕━━╮\n\n"
        f"🏆 {winner_mention} wins the toss!\n\n</b>"
        "<blockquote><b>"
        f"🎯 Call    ➤ {_CALL_LABEL[call]}\n"
        f"🪙 Result  ➤ {_CALL_LABEL[result]}"
        "</b></blockquote>\n"
        "<b>\nYour call, skipper. 🏏\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


@register_callback("play_toss_call")
async def on_play_toss_call(callback_query):
    _, match_id_str, call = callback_query["data"].split(":")
    match_id = int(match_id_str)
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    match = await get_match(match_id)
    if not match or match["status"] != "pitch_selected":
        await app.answer_callback_query(callback_query["id"], "The toss call is no longer active.", show_alert=True)
        return

    if int(presser["id"]) != int(match["opponent_id"]):
        await app.answer_callback_query(callback_query["id"], "Only the challenged player calls the toss!", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], f"You called {call}!")

    await app.edit_message_text(chat_id, message_id, f"<b>{_FRAME_1}</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await asyncio.sleep(1)
    await app.edit_message_text(chat_id, message_id, f"<b>{_FRAME_2}</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await asyncio.sleep(1)
    await app.edit_message_text(chat_id, message_id, f"<b>{_FRAME_3}</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await asyncio.sleep(1)

    result = flip_coin()
    winner_id = match["opponent_id"] if result == call else match["challenger_id"]
    await set_toss(match_id, winner_id, call, result)

    winner_username = match["opponent_username"] if result == call else match["challenger_username"]
    winner_name = match["opponent_name"] if result == call else match["challenger_name"]
    winner_mention = mention_html(winner_id, winner_username, winner_name)

    text = _toss_result_text(winner_mention, call, result)
    keyboard = bat_bowl_keyboard(match_id)
    await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=keyboard)

    match["toss_winner_id"] = winner_id
    match["toss_call"] = call
    match["toss_result"] = result


@register_callback("play_decision")
async def on_play_decision(callback_query):
    _, match_id_str, decision = callback_query["data"].split(":")
    match_id = int(match_id_str)
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    match = await get_match(match_id)
    if not match or match["status"] != "toss_done":
        await app.answer_callback_query(callback_query["id"], "This decision is no longer active.", show_alert=True)
        return

    if int(presser["id"]) != int(match["toss_winner_id"]):
        await app.answer_callback_query(callback_query["id"], "Only the toss winner decides!", show_alert=True)
        return

    await set_decision(match_id, decision)
    match["decision"] = decision
    await app.answer_callback_query(callback_query["id"], f"You chose to {decision}!")

    try:
        await app.delete_message(chat_id, message_id)
    except Exception as exc:
        print(f"[play] Failed to delete toss result message: {exc!r}")

    winner_is_challenger = int(match["toss_winner_id"]) == int(match["challenger_id"])
    winner_mention = (
        mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])
        if winner_is_challenger
        else mention_html(match["opponent_id"], match["opponent_username"], match["opponent_name"])
    )

    if decision == "bat":
        batting_is_challenger = winner_is_challenger
    else:
        batting_is_challenger = not winner_is_challenger

    batting_first_mention = (
        mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])
        if batting_is_challenger
        else mention_html(match["opponent_id"], match["opponent_username"], match["opponent_name"])
    )

    decision_word = "BAT" if decision == "bat" else "BOWL"
    text = (
        "<b>🏏 TOSS DECISION\n\n"
        f"🎯 {winner_mention} chose to {decision_word}\n"
        f"🏏 {batting_first_mention} will BAT first\n\n"
        "🔒 Decision Locked</b>"
    )
    await app.send_message(chat_id, text, parse_mode="HTML")

    await asyncio.sleep(2)
    await send_playing_xi(chat_id, match)
