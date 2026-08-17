print("endgame.py loaded")

from handlers.registry import register, register_callback
from app import app
from engines.match_engine import MATCH_ENGINE
from database.challenges_repo import update_status
from utils.timers import cancel_timer
from buttons.endgame_buttons import endgame_confirm_keyboard

NO_KEYBOARD = {"inline_keyboard": []}


def _is_match_participant(session, user_id) -> bool:
    return user_id in {session.challenger.user_id, session.opponent.user_id}


@register("endgame")
async def endgame_command(message):
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")

    print(f"[endgame] Command invoked by user_id={user_id} in chat_id={chat_id}")

    session = MATCH_ENGINE.get_session(chat_id)
    if session is None:
        await app.send_message(chat_id, "⚠️ There's no active match in this chat to end.")
        return

    if not _is_match_participant(session, user_id):
        await app.send_message(chat_id, "⚠️ You're not part of this match.")
        return

    await app.send_message(
        chat_id,
        "*⚠️ END MATCH?*\n\n"
        "Do you want to end this game?\n"
        "*This may cause heavy loss in your account balance.*",
        parse_mode="Markdown",
        reply_markup=endgame_confirm_keyboard(chat_id),
    )
    print(f"[endgame] Confirmation prompt sent to chat_id={chat_id}, requested by user_id={user_id}")


async def _finalize_endgame(chat_id: int, ender_user_id: int):
    session = MATCH_ENGINE.get_session(chat_id)
    if session is None:
        return

    if ender_user_id == session.challenger.user_id:
        winner_display = session.opponent.display()
        loser_display = session.challenger.display()
    else:
        winner_display = session.challenger.display()
        loser_display = session.opponent.display()

    text_lines = [
        "*🏳️ MATCH ENDED*",
        "",
        f"*{loser_display}* ended the match early.",
        f"*Winner:* {winner_display}",
    ]
    if session.innings is not None:
        text_lines.append("")
        text_lines.append(f"*Final Score:* {session.innings.score.runs}/{session.innings.score.wickets} ({session.innings.score.over_text} ov)")

    if session.challenge_id:
        try:
            await update_status(session.challenge_id, "ended")
        except Exception as exc:
            print(f"[endgame] Failed to update challenge status: {exc!r}")
        cancel_timer("challenge", session.challenge_id)
        cancel_timer("toss_call", session.challenge_id)
        cancel_timer("decision", session.challenge_id)

    await app.send_message(chat_id, "\n".join(text_lines), parse_mode="Markdown")
    MATCH_ENGINE.clear_session(chat_id)
    print(f"[endgame] Match ended by user_id={ender_user_id} in chat_id={chat_id}")


@register_callback("endgame_yes")
async def on_endgame_yes(callback_query):
    chat_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    message = callback_query.get("message") or {}
    message_id = message.get("message_id")

    print(f"[endgame] endgame_yes clicked by user_id={presser['id']} in chat_id={chat_id}")

    session = MATCH_ENGINE.get_session(chat_id)
    if session is None:
        await app.answer_callback_query(callback_query["id"], "This match is no longer active.", show_alert=True)
        if message_id:
            await app.edit_message_text(chat_id, message_id, "⚠️ This match is no longer active.", reply_markup=NO_KEYBOARD)
        return

    if not _is_match_participant(session, presser["id"]):
        await app.answer_callback_query(callback_query["id"], "🚫 You're not part of this match.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Ending match...")
    if message_id:
        await app.edit_message_text(chat_id, message_id, "*🏳️ Ending the match...*", parse_mode="Markdown", reply_markup=NO_KEYBOARD)

    await _finalize_endgame(chat_id, presser["id"])


@register_callback("endgame_cancel")
async def on_endgame_cancel(callback_query):
    chat_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    message = callback_query.get("message") or {}
    message_id = message.get("message_id")

    print(f"[endgame] endgame_cancel clicked by user_id={presser['id']} in chat_id={chat_id}")

    session = MATCH_ENGINE.get_session(chat_id)
    if session is not None and not _is_match_participant(session, presser["id"]):
        await app.answer_callback_query(callback_query["id"], "🚫 You're not part of this match.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Cancelled. The match continues.")
    if message_id:
        await app.edit_message_text(chat_id, message_id, "*✅ Match continues.* The end request was cancelled.", parse_mode="Markdown", reply_markup=NO_KEYBOARD)
