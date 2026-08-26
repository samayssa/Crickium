from __future__ import annotations

import asyncio
from typing import Any

from app import app
from handlers.registry import register

from database.play_repo import get_active_match_for_user as get_play_active_for_user, set_message_id as set_play_message_id
from database.playint_repo import get_active_match_for_user as get_playint_active_for_user, set_message_id as set_playint_message_id


RESUME_NOTICE = "<b>🔄 Game resuming...</b>"
NO_GAME_MESSAGE = (
    "<b>⚠️ We are not currently in any game to resume.\n"
    "Please play the game or start a new game.</b>"
)
SESSION_UNAVAILABLE_MESSAGE = (
    "<b>⚠️ This game is active, but its live session is unavailable.\n"
    "The exact game state cannot be restored right now.</b>"
)


async def _get_session_for_user(user_id: int, chat_id: int | None = None):
    """Resolve the current live in-memory session for a user.

    DB lookup identifies which engine/match the user is currently in. The
    actual delivery-by-delivery state remains in the in-memory session, so we
    only resume when that authoritative live session is still present.
    """
    uid = int(user_id)

    # Prefer the current chat so /resume in the game group is instantaneous.
    if chat_id is not None:
        from engines.play_runtime import get_session_in_chat
        from engines.playint_runtime import get_playint_session_in_chat

        play_session = get_session_in_chat(int(chat_id))
        if play_session is not None and uid in {
            int(play_session.match.get("challenger_id") or 0),
            int(play_session.match.get("opponent_id") or 0),
        }:
            return "PLAY", play_session

        playint_session = get_playint_session_in_chat(int(chat_id))
        if playint_session is not None and uid in {
            int(playint_session.match.get("challenger_id") or 0),
            int(playint_session.match.get("opponent_id") or 0),
        }:
            return "PLAYINT", playint_session

    # A user may issue /resume from DM while their match is running in a
    # group. DB resolves the match, then the live session supplies the exact
    # point-in-game state.
    play_match = await get_play_active_for_user(uid)
    if play_match:
        match_id = int(play_match["match_id"])
        from engines.play_runtime import get_session
        session = get_session(match_id)
        if session is not None:
            return "PLAY", session
        return "PLAY_UNAVAILABLE", play_match

    playint_match = await get_playint_active_for_user(uid)
    if playint_match:
        match_id = int(playint_match["match_id"])
        from engines.playint_runtime import get_playint_session
        session = get_playint_session(match_id)
        if session is not None:
            return "PLAYINT", session
        return "PLAYINT_UNAVAILABLE", playint_match

    return None, None


def _resume_markup(engine: str, session: Any):
    match_id = int(session.match_id)
    if engine == "PLAY":
        from buttons.play_buttons import (
            bowler_selection_keyboard,
            bowler_tactic_keyboard,
            strategy_keyboard,
        )
        from engines.play_runtime import next_bowler_card
    else:
        from buttons.playint_buttons import (
            bowler_selection_keyboard,
            bowler_tactic_keyboard,
            strategy_keyboard,
        )
        from engines.playint_runtime import next_bowler_card

    stage = str(session.stage or "choose_bowler")
    if stage == "choose_bowler":
        return bowler_selection_keyboard(match_id, next_bowler_card(session))
    if stage == "choose_tactic":
        return bowler_tactic_keyboard(match_id, session.current_bowler)
    if stage == "choose_strategy":
        return strategy_keyboard(match_id)
    return {"inline_keyboard": []}


def _render_resume_scorecard(engine: str, session: Any) -> str:
    if engine == "PLAY":
        from engines.play_runtime import render_live_scorecard
    else:
        from engines.playint_runtime import render_live_scorecard
    return render_live_scorecard(
        session,
        bowler_prompt=(str(session.stage or "") == "choose_bowler"),
    )


async def _update_live_message_id(engine: str, session: Any, message_id: int) -> None:
    try:
        if engine == "PLAY":
            await set_play_message_id(int(session.match_id), int(message_id))
        else:
            await set_playint_message_id(int(session.match_id), int(message_id))
    except Exception as exc:
        # The in-memory session remains authoritative for the running match;
        # DB message-id persistence is a best-effort recovery aid.
        print(f"[resume] Failed to persist new live message id for {engine}: {exc!r}")


@register("resume")
async def resume_command(message):
    user = message.get("from") or {}
    chat = message.get("chat") or {}
    user_id = int(user.get("id") or 0)
    chat_id = int(chat.get("id") or 0)

    engine, session_or_match = await _get_session_for_user(user_id, chat_id)

    if engine is None:
        await app.send_message(chat_id, NO_GAME_MESSAGE, parse_mode="HTML")
        return

    if engine.endswith("_UNAVAILABLE"):
        await app.send_message(chat_id, SESSION_UNAVAILABLE_MESSAGE, parse_mode="HTML")
        return

    session = session_or_match
    match = session.match
    participants = {
        int(match.get("challenger_id") or 0),
        int(match.get("opponent_id") or 0),
    }
    if user_id not in participants:
        await app.send_message(chat_id, NO_GAME_MESSAGE, parse_mode="HTML")
        return

    # Guard against a stale session that has already completed while the
    # database row is waiting for final cleanup.
    if getattr(session.innings, "completed", False):
        await app.send_message(chat_id, NO_GAME_MESSAGE, parse_mode="HTML")
        return

    old_live_message_id = getattr(session, "live_message_id", None)
    try:
        if old_live_message_id:
            await app.delete_message(session.chat_id, int(old_live_message_id))
    except Exception as exc:
        print(f"[resume] Failed to delete old live score message: {exc!r}")

    notice = None
    try:
        notice = await app.send_message(session.chat_id, RESUME_NOTICE, parse_mode="HTML")
    except Exception as exc:
        print(f"[resume] Failed to send resume notice: {exc!r}")

    try:
        live = await app.send_message(
            session.chat_id,
            _render_resume_scorecard(engine, session),
            parse_mode="HTML",
            reply_markup=_resume_markup(engine, session),
        )
        new_message_id = live.get("message_id")
        if new_message_id:
            session.live_message_id = int(new_message_id)
            await _update_live_message_id(engine, session, int(new_message_id))
    finally:
        if notice and notice.get("message_id"):
            await asyncio.sleep(0.8)
            try:
                await app.delete_message(session.chat_id, int(notice["message_id"]))
            except Exception as exc:
                print(f"[resume] Failed to delete resume notice: {exc!r}")
