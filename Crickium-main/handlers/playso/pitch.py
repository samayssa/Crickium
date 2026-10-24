from __future__ import annotations

import asyncio
from handlers.registry import register_callback
from app import app
from buttons.playso_buttons import pitch_keyboard
from database.playso_repo import get_match, update_locked, set_basic
from engines.play_engine import pitch_label
from utils.mentions import mention_html
from handlers.playso.common import callback_message_is_current, match_lock

NO_KEYBOARD = {"inline_keyboard": []}


def pitch_text(match: dict, selected: str | None = None) -> str:
    challenger = mention_html(match["challenger_id"], match.get("challenger_username"), match.get("challenger_name"))
    chosen = f"\n\n<b>✅ Selected ➤ {pitch_label(selected)} Pitch</b>" if selected else ""
    return ("<b>╭━━〔 🏟️ PLAYSO • PITCH SELECT 〕━━╮\n\n"
            f"👤 {challenger}\n\n"
            "Choose the battlefield for your Super Over.\n"
            "The selected pitch remains active for both innings. ⚡"
            f"{chosen}\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>")


async def send_pitch_selection(chat_id: int, match: dict):
    sent = await app.send_message(chat_id, pitch_text(match), parse_mode="HTML", reply_markup=pitch_keyboard(int(match["match_id"])))
    from database.playso_repo import set_message_id
    await set_message_id(int(match["match_id"]), int(sent["message_id"]))


@register_callback("playso_pitch")
async def playso_pitch(callback_query):
    _, mid, pitch = callback_query["data"].split(":"); mid = int(mid); uid = int(callback_query["from"]["id"])
    msg = callback_query["message"]; match = await get_match(mid)
    async with match_lock(mid):
        match = await get_match(mid)
        if not match or match["status"] != "accepted":
            await app.answer_callback_query(callback_query["id"], "Pitch selection is no longer active.", show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        if uid != int(match["challenger_id"]):
            await app.answer_callback_query(callback_query["id"], "Only the challenger selects the pitch.", show_alert=True); return
        if pitch not in {"green", "dry", "dusty", "flat", "hard", "even", "bouncy", "slow"}:
            await app.answer_callback_query(callback_query["id"], "Invalid pitch selection.", show_alert=True); return

        def updater(data, state):
            state["pitch"] = pitch
            state["stage"] = "toss_call"
            return {"pitch": pitch}, "pitch_selected"

        fresh, result = await update_locked(mid, {"accepted"}, updater)
        if result == "stale":
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        await set_basic(mid, pitch=pitch)
        fresh = await get_match(mid)
        await app.answer_callback_query(callback_query["id"], "Pitch locked!")
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], pitch_text(dict(fresh), pitch), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await asyncio.sleep(1)
        from .toss import send_toss_call
        await send_toss_call(msg["chat"]["id"], dict(fresh))
