from __future__ import annotations

import html

from handlers.registry import register, register_callback
from app import app
from config import ADMIN_USER_ID
from database.play_repo import (
    get_active_match_in_chat as get_play_match_in_chat,
    get_match as get_play_match,
    update_status as update_play_status,
)
from database.playint_repo import (
    get_active_match_in_chat as get_playint_match_in_chat,
    get_match as get_playint_match,
    update_status as update_playint_status,
)
from buttons.endgame_buttons import abandon_confirm_keyboard
from engines.play_runtime import get_session as get_play_session, clear_session
from engines.playint_runtime import get_playint_session, clear_playint_session
from engines.play_engine import pitch_label
from utils.mentions import mention_html


NO_KEYBOARD = {"inline_keyboard": []}
_TERMINAL = {"declined", "completed", "ended"}


def _owner_only(user_id: int | None) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


def _name_mention(match: dict, prefix: str) -> str:
    return mention_html(
        match.get(f"{prefix}_id"),
        match.get(f"{prefix}_username"),
        match.get(f"{prefix}_name"),
    )


def _score_text(innings) -> str:
    if innings is None:
        return "Not yet played"
    score = getattr(innings, "score", None)
    if score is None:
        return "Not yet played"
    runs = int(getattr(score, "runs", 0) or 0)
    wickets = int(getattr(score, "wickets", 0) or 0)
    legal_balls = int(getattr(score, "legal_balls", 0) or 0)
    overs = f"{legal_balls // 6}.{legal_balls % 6}"
    return f"{runs}/{wickets} ({overs})"


def _snapshot_score(snapshot: dict | None) -> str:
    if not snapshot:
        return "Not yet played"
    return f"{int(snapshot.get('runs') or 0)}/{int(snapshot.get('wickets') or 0)} ({snapshot.get('over_text') or '0.0'} Ov)"


def _game_summary(
    match: dict,
    *,
    engine: str,
    session=None,
) -> str:
    a = _name_mention(match, "challenger")
    o = _name_mention(match, "opponent")

    pitch_code = match.get("pitch")
    if not pitch_code and session is not None:
        pitch_code = getattr(session, "pitch", None)
    pitch = "Not selected yet" if not pitch_code else html.escape(pitch_label(str(pitch_code)))

    if engine == "play":
        mode = "PLAY • T20 1v1"
        first_text = "Not yet played"
        second_text = "Not yet played"
    else:
        mode = "PLAYINT • T20 International"
        first_text = "Not yet played"
        second_text = "Not yet played"

    if session is not None:
        innings_number = int(getattr(getattr(session, "innings", None), "innings_number", 1) or 1)
        history = list(getattr(session, "innings_history", []) or [])
        if innings_number == 1:
            first_text = _score_text(getattr(session, "innings", None))
        else:
            first_text = _snapshot_score(history[0] if history else None)
            second_text = _score_text(getattr(session, "innings", None))

    return (
        "<b>╭━━〔 🏳️ ABANDON GAME 〕━━╮</b>\n\n"
        f"<b>⚔️ {a}\n"
        f"🔥 {o}</b>\n\n"
        "<blockquote><b>"
        f"🎮 Mode ➤ {html.escape(mode)}\n"
        f"🏟️ Pitch ➤ {pitch}\n"
        f"1️⃣ First Innings ➤ {first_text}\n"
        f"2️⃣ Second Innings ➤ {second_text}"
        "</b></blockquote>\n\n"
        "<b>Are you sure you want to abandon this game?\n"
        "The current live game will end with no winner.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _find_active(chat_id: int):
    match = await get_play_match_in_chat(chat_id)
    if match:
        return dict(match), "play"
    match = await get_playint_match_in_chat(chat_id)
    if match:
        return dict(match), "playint"
    return None, None


async def _clear_live_messages(chat_id: int, match_id: int, engine: str) -> None:
    session = get_play_session(match_id) if engine == "play" else get_playint_session(match_id)
    if not session:
        return

    message_ids = {
        getattr(session, "live_message_id", None),
        getattr(session, "ready_message_id", None),
        getattr(session, "short_message_id", None),
    }
    for message_id in message_ids:
        if not message_id:
            continue
        try:
            await app.delete_message(chat_id, int(message_id))
        except Exception as exc:
            print(
                f"[abandon] Failed deleting {engine} live message "
                f"{message_id} for match_id={match_id}: {exc!r}"
            )

    if engine == "play":
        clear_session(match_id)
    else:
        clear_playint_session(match_id)


@register("abandon")
async def abond_command(message):
    user_id = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    chat_type = str((message.get("chat") or {}).get("type") or "").lower()

    if not _owner_only(user_id):
        await app.send_message(
            chat_id,
            "<b>🚫 This command is restricted to the bot owner/admin.</b>",
            parse_mode="HTML",
        )
        return

    if chat_type not in {"group", "supergroup"}:
        await app.send_message(
            chat_id,
            "<b>⚠️ /abond can only be used inside a group game.</b>",
            parse_mode="HTML",
        )
        return

    match, engine = await _find_active(chat_id)
    if not match:
        await app.send_message(
            chat_id,
            "<b>⚠️ There is no active game in this group.</b>",
            parse_mode="HTML",
        )
        return

    session = get_play_session(match["match_id"]) if engine == "play" else get_playint_session(match["match_id"])
    text = _game_summary(match, engine=engine, session=session)
    await app.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=abandon_confirm_keyboard(chat_id),
    )


@register_callback("abandon_yes")
async def abandon_yes(callback_query):
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    chat_id = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    message_id = int((callback_query.get("message") or {}).get("message_id") or 0)

    if not _owner_only(user_id):
        await app.answer_callback_query(
            callback_query["id"],
            "🚫 Only the bot owner/admin can abandon a game.",
            show_alert=True,
        )
        return

    match, engine = await _find_active(chat_id)
    if not match:
        await app.answer_callback_query(
            callback_query["id"],
            "There is no active game here.",
            show_alert=True,
        )
        await app.edit_message_text(
            chat_id,
            message_id,
            "<b>⚠️ No active game found. The group is already clear.</b>",
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
        return

    mid = int(match["match_id"])
    try:
        if engine == "play":
            await update_play_status(mid, "ended")
        else:
            await update_playint_status(mid, "ended")
    except Exception as exc:
        print(f"[abandon] Failed to mark {engine} match_id={mid} ended: {exc!r}")
        await app.answer_callback_query(
            callback_query["id"],
            "Could not end the game safely.",
            show_alert=True,
        )
        return

    await _clear_live_messages(chat_id, mid, engine)

    await app.answer_callback_query(
        callback_query["id"],
        "Game abandoned. No winner, no loser.",
    )

    a = _name_mention(match, "challenger")
    o = _name_mention(match, "opponent")
    final_text = (
        "<b>╭━━〔 🏳️ GAME ABANDONED 〕━━╮</b>\n\n"
        f"<b>⚔️ {a}\n"
        f"🔥 {o}</b>\n\n"
        "<blockquote><b>"
        "🚫 No winner\n"
        "🚫 No loser\n"
        "🪙 No penalty\n"
        "✨ Both players have been released from the game."
        "</b></blockquote>\n\n"
        "<b>The group is ready for a fresh game. 🏏\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    await app.edit_message_text(
        chat_id,
        message_id,
        final_text,
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )


@register_callback("abandon_cancel")
async def abandon_cancel(callback_query):
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    chat_id = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    message_id = int((callback_query.get("message") or {}).get("message_id") or 0)

    if not _owner_only(user_id):
        await app.answer_callback_query(
            callback_query["id"],
            "🚫 Only the bot owner/admin can cancel this.",
            show_alert=True,
        )
        return

    await app.answer_callback_query(
        callback_query["id"],
        "Cancelled. The game continues.",
    )
    await app.edit_message_text(
        chat_id,
        message_id,
        "<b>✅ Abandon request cancelled.\nThe current game continues.</b>",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
