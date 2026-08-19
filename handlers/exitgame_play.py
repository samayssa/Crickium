from __future__ import annotations

print("exitgame_play.py loaded")

from handlers.registry import register, register_callback
from app import app
from database.query import execute
from database.play_repo import get_active_match_in_chat, get_match, update_status
from database.user_stats_repo import add_match_xp, record_match_result
from engines.level_engine import WIN_XP, EXIT_PENALTY_XP
from engines.play_runtime import clear_session, get_session
from utils.mentions import mention_html
from buttons.play_buttons import exit_confirm_keyboard

NO_KEYBOARD = {"inline_keyboard": []}
EXIT_PENALTY = 5000


def _is_participant(match: dict, user_id: int) -> bool:
    return int(user_id) in (int(match["challenger_id"]), int(match["opponent_id"]))


@register("exitgame")
async def exitgame_command(message):
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id")

    print(f"[exitgame_play] /exitgame invoked by user_id={user_id} in chat_id={chat_id}")

    match = await get_active_match_in_chat(chat_id)
    if not match:
        await app.send_message(chat_id, "<b>⚠️ There's no active /play match in this chat to exit.</b>", parse_mode="HTML")
        return

    if not _is_participant(match, user_id):
        await app.send_message(chat_id, "<b>⚠️ You're not part of this match.</b>", parse_mode="HTML")
        return

    await app.send_message(
        chat_id,
        (
            "<b>⚠️ EXIT GAME?\n\n"
            "Are you sure you want to exit the game?\n"
            f"Result: you will receive -{EXIT_PENALTY:,} coins penalty</b>"
        ),
        parse_mode="HTML",
        reply_markup=exit_confirm_keyboard(match["match_id"]),
    )
    print(f"[exitgame_play] Confirmation prompt sent to chat_id={chat_id}, requested by user_id={user_id}")


@register_callback("play_exit_yes")
async def on_play_exit_yes(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    print(f"[exitgame_play] play_exit_yes clicked by user_id={presser['id']} for match_id={match_id}")

    match = await get_match(match_id)
    if not match or match["status"] in {"declined", "completed", "ended"}:
        await app.answer_callback_query(callback_query["id"], "This match is no longer active.", show_alert=True)
        await app.edit_message_text(chat_id, message_id, "<b>⚠️ This match is no longer active.</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
        return

    if not _is_participant(match, presser["id"]):
        await app.answer_callback_query(callback_query["id"], "🚫 You're not part of this match.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Exiting the game...")

    try:
        await execute(
            "UPDATE users SET balance = balance - $1 WHERE user_id = $2;",
            EXIT_PENALTY, presser["id"],
        )
    except Exception as exc:
        print(f"[exitgame_play] Failed to apply coin penalty to user_id={presser['id']}: {exc!r}")

    try:
        await update_status(match_id, "ended")
    except Exception as exc:
        print(f"[exitgame_play] Failed to update match_id={match_id} status to ended: {exc!r}")

    # XP + stats: the exiter gets nothing for this match, the player who
    # stayed is treated as the winner (full win XP + a recorded win).
    try:
        stayed_id = match["opponent_id"] if int(presser["id"]) == int(match["challenger_id"]) else match["challenger_id"]
        await add_match_xp(presser["id"], EXIT_PENALTY_XP)
        await add_match_xp(stayed_id, WIN_XP)
        await record_match_result(presser["id"], won=False)
        await record_match_result(stayed_id, won=True)
    except Exception as exc:
        print(f"[exitgame_play] Failed to award XP/stats for match_id={match_id}: {exc!r}")

    session = get_session(match_id)
    exiter_mention = mention_html(presser["id"], presser.get("username"), presser.get("first_name"))
    stayed_id = match["opponent_id"] if int(presser["id"]) == int(match["challenger_id"]) else match["challenger_id"]
    stayed_mention = mention_html(stayed_id, match.get("opponent_username") if int(stayed_id) == int(match["opponent_id"]) else match.get("challenger_username"), match.get("opponent_name") if int(stayed_id) == int(match["opponent_id"]) else match.get("challenger_name"))

    # Remove the live scorecard immediately so the result/highlights becomes
    # the final match card shown in the chat.
    if session and session.live_message_id:
        try:
            await app.delete_message(chat_id, session.live_message_id)
        except Exception as exc:
            print(f"[exitgame_play] Failed to delete live scorecard for match_id={match_id}: {exc!r}")

    if session:
        try:
            from handlers.play.live import _exit_match_result_text
            highlights = _exit_match_result_text(session, exiter_mention, stayed_mention)
            await app.edit_message_text(chat_id, message_id, highlights, parse_mode="HTML", reply_markup=NO_KEYBOARD)
        except Exception as exc:
            print(f"[exitgame_play] Failed to render exit highlights for match_id={match_id}: {exc!r}")
            await app.edit_message_text(
                chat_id,
                message_id,
                (
                    "<b>🏳️ MATCH ENDED\n\n"
                    f"{exiter_mention} exited the game.\n"
                    f"Penalty applied: -{EXIT_PENALTY:,} coins 🪙</b>"
                ),
                parse_mode="HTML",
                reply_markup=NO_KEYBOARD,
            )
    else:
        await app.edit_message_text(
            chat_id,
            message_id,
            (
                "<b>🏳️ MATCH ENDED\n\n"
                f"{exiter_mention} exited the game.\n"
                f"Penalty applied: -{EXIT_PENALTY:,} coins 🪙</b>"
            ),
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )

    try:
        clear_session(match_id)
    except Exception as exc:
        print(f"[exitgame_play] Failed to clear play session for match_id={match_id}: {exc!r}")
    print(f"[exitgame_play] Match ended by user_id={presser['id']} in chat_id={chat_id}, match_id={match_id}")


@register_callback("play_exit_cancel")
async def on_play_exit_cancel(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    print(f"[exitgame_play] play_exit_cancel clicked by user_id={presser['id']} for match_id={match_id}")

    match = await get_match(match_id)
    if match and not _is_participant(match, presser["id"]):
        await app.answer_callback_query(callback_query["id"], "🚫 You're not part of this match.", show_alert=True)
        return

    await app.answer_callback_query(callback_query["id"], "Cancelled. The match continues.")
    await app.edit_message_text(
        chat_id,
        message_id,
        "<b>✅ Match continues.\nThe exit request was cancelled.</b>",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
