from __future__ import annotations

import asyncio
from typing import Any

from app import app
from handlers.registry import register

from database.play_repo import get_active_match_for_user as get_play_active_for_user, set_message_id as set_play_message_id
from database.playint_repo import get_active_match_for_user as get_playint_active_for_user, set_message_id as set_playint_message_id
from database.playipl_repo import get_active_match_for_user as get_playipl_active_for_user, set_message_id as set_playipl_message_id
from database.playso_repo import get_active_match_for_user as get_playso_active_for_user, set_message_id as set_playso_message_id


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
        from engines.playipl_runtime import get_playipl_session_in_chat

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

        playipl_session = get_playipl_session_in_chat(int(chat_id))
        if playipl_session is not None and uid in {
            int(playipl_session.match.get("challenger_id") or 0),
            int(playipl_session.match.get("opponent_id") or 0),
        }:
            return "PLAYIPL", playipl_session

        playso_match = await get_playso_active_for_user(uid)
        if playso_match and str(playso_match.get("status") or "") != "pending" and int(playso_match.get("chat_id") or 0) == int(chat_id):
            return "PLAYSO", dict(playso_match)

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

    playipl_match = await get_playipl_active_for_user(uid)
    if playipl_match:
        match_id = int(playipl_match["match_id"])
        from engines.playipl_runtime import get_playipl_session
        session = get_playipl_session(match_id)
        if session is not None:
            return "PLAYIPL", session
        return "PLAYIPL_UNAVAILABLE", playipl_match

    playso_match = await get_playso_active_for_user(uid)
    if playso_match and str(playso_match.get("status") or "") != "pending":
        return "PLAYSO", dict(playso_match)

    return None, None


def _resume_markup(engine: str, session: Any):
    if engine == "PLAYSO":
        from buttons.playso_buttons import (
            pitch_keyboard,
            toss_keyboard,
            decision_keyboard,
            bowler_selection_keyboard,
            batter_selection_keyboard,
            length_keyboard,
            delivery_keyboard,
            line_keyboard,
            foot_keyboard,
            intent_keyboard,
            shot_keyboard,
        )
        from engines.playso_probability import deliveries_for_family, shots_for
        from handlers.playso.common import current_xi, bowling_family

        match = session
        state = match.get("state") or {}
        stage = str(state.get("stage") or "")
        mid = int(match["match_id"])
        if match.get("status") == "accepted" or stage == "pitch":
            return pitch_keyboard(mid)
        if match.get("status") == "pitch_selected" or stage == "toss_call":
            return toss_keyboard(mid)
        if match.get("status") == "toss_done" or stage == "decision":
            return decision_keyboard(mid)
        if match.get("status") == "lineup" and stage == "bowler_select":
            players = state.get("bowling_xi") or []
            return bowler_selection_keyboard(mid, players, state.get("selected_bowler"), state.get("locked_bowler"))
        if match.get("status") == "lineup" and stage == "batter_select":
            return batter_selection_keyboard(mid, state.get("batting_xi") or [], [int(x) for x in state.get("selected_batters") or []])
        if match.get("status") == "live":
            if stage == "length":
                return length_keyboard(mid)
            if stage == "delivery":
                bowler = next((p for p in state.get("bowling_xi") or [] if int(p.get("player_id") or 0) == int(state.get("selected_bowler") or 0)), {})
                return delivery_keyboard(mid, deliveries_for_family(bowling_family(bowler), str(state.get("length") or "full")))
            if stage == "line":
                return line_keyboard(mid)
            if stage == "foot":
                return foot_keyboard(mid)
            if stage == "intent":
                return intent_keyboard(mid)
            if stage == "shot":
                return shot_keyboard(mid, shots_for(str(state.get("foot") or "front"), str(state.get("intent") or "grounded")))
        return {"inline_keyboard": []}

    match_id = int(session.match_id)
    if engine == "PLAY":
        from buttons.play_buttons import (
            bowler_selection_keyboard,
            bowler_tactic_keyboard,
            strategy_keyboard,
        )
        from engines.play_runtime import next_bowler_card
    elif engine == "PLAYINT":
        from buttons.playint_buttons import (
            bowler_selection_keyboard,
            bowler_tactic_keyboard,
            strategy_keyboard,
        )
        from engines.playint_runtime import next_bowler_card
    else:
        from buttons.playipl_buttons import (
            bowler_selection_keyboard,
            bowler_tactic_keyboard,
            strategy_keyboard,
        )
        from engines.playipl_runtime import next_bowler_card

    stage = str(session.stage or "choose_bowler")
    if stage == "choose_bowler":
        return bowler_selection_keyboard(match_id, next_bowler_card(session))
    if stage == "choose_tactic":
        return bowler_tactic_keyboard(match_id, session.current_bowler)
    if stage == "choose_strategy":
        return strategy_keyboard(match_id)
    return {"inline_keyboard": []}


def _render_resume_scorecard(engine: str, session: Any) -> str:
    if engine == "PLAYSO":
        from handlers.playso.common import _innings_score
        from utils.mentions import mention_html
        match = session
        state = match.get("state") or {}
        status = str(match.get("status") or "")
        challenger = mention_html(match["challenger_id"], match.get("challenger_username"), match.get("challenger_name"))
        opponent = mention_html(match["opponent_id"], match.get("opponent_username"), match.get("opponent_name"))
        if status == "accepted" or state.get("stage") == "pitch":
            return f"<b>╭━━〔 ⚡ PLAYSO • RESUME 〕━━╮\n\n{challenger}\n\nChoose the pitch for your Super Over.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        stage = str(state.get("stage") or "")
        if status == "pitch_selected" or stage == "toss_call":
            return f"<b>╭━━〔 🪙 PLAYSO • TOSS CALL 〕━━╮\n\n👤 {opponent}\n\nCall the coin to continue your Super Over.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        if status == "toss_done" or stage == "decision":
            winner = int(match.get("toss_winner_id") or 0)
            mention = challenger if winner == int(match["challenger_id"]) else opponent
            return f"<b>🏆 {mention} won the toss.\n\nChoose BAT or BOWL to continue.</b>"
        if status == "lineup":
            batting = int(state.get("batting_user") or 0)
            bowling = int(state.get("bowling_user") or 0)
            user = challenger if batting == int(match["challenger_id"]) else opponent
            if stage == "bowler_select":
                return f"<b>╭━━〔 🎯 PLAYSO • BOWLER SELECT 〕━━╮\n\n👤 {opponent if bowling == int(match['opponent_id']) else challenger}\n\nChoose your one bowler for this Super Over.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
            return f"<b>╭━━〔 🏏 PLAYSO • BATTERS 〕━━╮\n\n👤 {user}\n\nSelect your three batters.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        if status == "live":
            from handlers.playso.live import _score_lines, _bowler_flow_text, _batter_flow_text
            if stage in {"length", "delivery", "line"}:
                body = _score_lines(state)
                if state.get("length"): body.append(f"<b>📏 Length ➤ {str(state['length']).replace('_',' ').upper()}</b>")
                if state.get("delivery"): body.append(f"<b>🥎 Delivery ➤ {state['delivery']}</b>")
                if state.get("line"): body.append(f"<b>📍 Line ➤ {str(state['line']).replace('_',' ').upper()}</b>")
                return _bowler_flow_text(match, state, f"RESUME • {stage.upper()}", body + ["<b>Continue your bowling decision.</b>"])
            body = _score_lines(state)
            if state.get("delivery"): body.append(f"<b>🥎 Delivery ➤ {state['delivery']}</b>")
            if state.get("length"): body.append(f"<b>📏 Length ➤ {str(state['length']).replace('_',' ').upper()}</b>")
            if state.get("line"): body.append(f"<b>📍 Line ➤ {str(state['line']).replace('_',' ').upper()}</b>")
            if state.get("foot"): body.append(f"<b>👣 Foot ➤ {str(state['foot']).upper()}</b>")
            if state.get("intent"): body.append(f"<b>🔥 Intent ➤ {str(state['intent']).upper()}</b>")
            return _batter_flow_text(match, state, f"RESUME • {stage.upper()}", body + ["<b>Continue your batting decision.</b>"])
        if status == "innings_break":
            history = list(state.get("innings_history") or [])
            snap = history[-1] if history else {}
            return f"<b>╭━━〔 🔄 PLAYSO • INNINGS BREAK 〕━━╮\n\n🏏 First Innings ➤ {int(snap.get('runs') or 0)}/{int(snap.get('wickets') or 0)}\n🎯 Target ➤ {int(state.get('target') or 0)}\n\nSecond Super Over innings will continue shortly.\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        return f"<b>⚡ PLAYSO is active between {challenger} and {opponent}.</b>"

    if engine == "PLAY":
        from engines.play_runtime import render_live_scorecard
    elif engine == "PLAYINT":
        from engines.playint_runtime import render_live_scorecard
    else:
        from engines.playipl_runtime import render_live_scorecard
    return render_live_scorecard(
        session,
        bowler_prompt=(str(session.stage or "") == "choose_bowler"),
    )


async def _update_live_message_id(engine: str, session: Any, message_id: int) -> None:
    try:
        if engine == "PLAY":
            await set_play_message_id(int(session.match_id), int(message_id))
        elif engine == "PLAYINT":
            await set_playint_message_id(int(session.match_id), int(message_id))
        elif engine == "PLAYSO":
            await set_playso_message_id(int(session["match_id"]), int(message_id))
        else:
            await set_playipl_message_id(int(session.match_id), int(message_id))
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
    if engine == "PLAYSO":
        match = dict(session)
        participants = {int(match.get("challenger_id") or 0), int(match.get("opponent_id") or 0)}
        if user_id not in participants:
            await app.send_message(chat_id, NO_GAME_MESSAGE, parse_mode="HTML")
            return
        old_live_message_id = match.get("message_id")
        if old_live_message_id:
            try: await app.delete_message(int(match["chat_id"]), int(old_live_message_id))
            except Exception as exc: print(f"[resume] Failed deleting old PLAYSO message: {exc!r}")
        live = await app.send_message(int(match["chat_id"]), _render_resume_scorecard("PLAYSO", match), parse_mode="HTML", reply_markup=_resume_markup("PLAYSO", match))
        if live.get("message_id"):
            await set_playso_message_id(int(match["match_id"]), int(live["message_id"]))
        from utils.game_inactivity import sync_after_change
        await sync_after_change("PLAYSO", int(match["match_id"]), user_id)
        return

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
