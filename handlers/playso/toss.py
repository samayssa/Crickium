from __future__ import annotations

import asyncio
from handlers.registry import register_callback
from app import app
from buttons.playso_buttons import toss_keyboard
from database.playso_repo import get_match, update_locked
from engines.play_engine import flip_coin
from utils.mentions import mention_html
from handlers.playso.common import callback_message_is_current, match_lock

NO_KEYBOARD = {"inline_keyboard": []}

async def send_toss_call(chat_id: int, match: dict):
    user = mention_html(match["opponent_id"], match.get("opponent_username"), match.get("opponent_name"))
    sent = await app.send_message(chat_id, f"<b>╭━━〔 🪙 PLAYSO • TOSS CALL 〕━━╮\n\n👤 {user}\n\nCall the coin. Choose your side. 🏏\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>", parse_mode="HTML", reply_markup=toss_keyboard(int(match["match_id"])))
    from database.playso_repo import set_message_id
    await set_message_id(int(match["match_id"]), int(sent["message_id"]))


@register_callback("playso_toss")
async def playso_toss(callback_query):
    _, mid, call = callback_query["data"].split(":"); mid = int(mid); uid = int(callback_query["from"]["id"])
    msg = callback_query["message"]; match = await get_match(mid)
    if not match or match["status"] != "pitch_selected":
        await app.answer_callback_query(callback_query["id"], "The toss call is no longer active.", show_alert=True); return
    if not callback_message_is_current(dict(match), callback_query):
        await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
    if uid != int(match["opponent_id"]):
        await app.answer_callback_query(callback_query["id"], "Only the challenged player calls the toss.", show_alert=True); return
    async with match_lock(mid):
        winner = flip_coin()
        def updater(data, state):
            state["stage"] = "decision"
            return {"call":call,"result":winner}, "toss_done"
        fresh, status = await update_locked(mid, {"pitch_selected"}, updater)
        if status == "stale" or fresh is None:
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        winner_id = int(match["opponent_id"] if winner == call else match["challenger_id"])
        from database.playso_repo import set_basic
        await set_basic(mid, toss_winner_id=winner_id, toss_call=call, toss_result=winner)
        await app.answer_callback_query(callback_query["id"], "Toss called!")
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], "<b>🪙 Tossing the coin...\n↻  ◌  ↺</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await asyncio.sleep(.8)
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], "<b>🪙 And it's coming down...\n◒  ◉  ◐</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await asyncio.sleep(.8)
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], f"<b>🏆 Toss Result\n\n{mention_html(winner_id, match.get('opponent_username') if winner_id == int(match['opponent_id']) else match.get('challenger_username'), match.get('opponent_name') if winner_id == int(match['opponent_id']) else match.get('challenger_name'))} wins the toss.\n\nChoose your decision.</b>", parse_mode="HTML", reply_markup=__import__("buttons.playso_buttons", fromlist=["decision_keyboard"]).decision_keyboard(mid) if winner_id else NO_KEYBOARD)


@register_callback("playso_decision")
async def playso_decision(callback_query):
    _, mid, decision = callback_query["data"].split(":"); mid = int(mid); uid = int(callback_query["from"]["id"])
    match = await get_match(mid); msg = callback_query["message"]
    if not match or match["status"] != "toss_done":
        await app.answer_callback_query(callback_query["id"], "The decision stage is no longer active.", show_alert=True); return
    if not callback_message_is_current(dict(match), callback_query):
        await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
    if uid != int(match["toss_winner_id"]):
        await app.answer_callback_query(callback_query["id"], "Only the toss winner can decide.", show_alert=True); return
    async with match_lock(mid):
        def updater(data, state):
            state["stage"] = "setup"
            return {"decision":decision}, "lineup"
        fresh, status = await update_locked(mid, {"toss_done"}, updater)
        if status == "stale":
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        from database.playso_repo import set_basic
        await set_basic(mid, decision=decision, stadium=__import__("utils.stadium", fromlist=["random_stadium"]).random_stadium(), weather=__import__("utils.temperature", fromlist=["random_weather"]).random_weather().format())
        fresh = dict(await get_match(mid))
        await app.answer_callback_query(callback_query["id"], f"You chose to {decision} first!")
        from handlers.playso.common import send_match_ready
        await app.delete_message(msg["chat"]["id"], msg["message_id"])
        ready = await send_match_ready(msg["chat"]["id"], fresh)
        if ready.get("message_id"):
            try:
                await app.pin_chat_message(msg["chat"]["id"], ready["message_id"], disable_notification=True)
            except Exception as exc:
                print(f"[playso] Failed to pin MATCH READY card: {exc!r}")
        await asyncio.sleep(2)
        from .setup import start_setup
        fresh = dict(await get_match(mid))
        await start_setup(msg["chat"]["id"], fresh, innings_no=1)
