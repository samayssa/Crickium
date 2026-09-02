from __future__ import annotations

print("play/live.py loaded")

import asyncio
from typing import Any

from app import app
from buttons.play_buttons import bowler_selection_keyboard, bowler_tactic_keyboard, strategy_keyboard
from database.play_repo import get_match, update_status
from database.user_stats_repo import add_match_xp, record_match_result
from database.player_user_stats_repo import record_match_player_stats
from services.player_match_stats import record_session_player_stats
from engines.level_engine import WIN_XP, LOSS_XP, TIE_XP
from services.match_rewards import award_competitive_rewards, build_result_caption
from database.stadium_images_repo import get_stadium_image, save_stadium_image
from engines.lineup_engine import load_current_xi
from engines.play_engine import playing_xi, pitch_label
from engines.play_runtime import (
    assign_bowler,
    assign_tactic,
    clear_session,
    create_play_session,
    get_session,
    innings_completed,
    match_winner,
    next_bowler_card,
    over_complete_text,
    player_of_the_match,
    render_live_scorecard,
    simulate_ball,
    snapshot_innings,
    start_new_partnership,
    start_second_innings,
    top_batters,
    top_bowlers,
)
from services.search import find_stadium_image_url
from services.match_summary import send_match_summary, player_details
from utils.mentions import display_name, mention_html
from utils.stadium import random_stadium
from utils.temperature import random_weather
from handlers.registry import register_callback

NO_KEYBOARD = {"inline_keyboard": []}

# Minimum gap between edits to the SAME live-scorecard message during
# ball-by-ball simulation. Telegram throttles rapid edits to one
# message (~1/sec in practice, stricter under load); app.py already
# retries on FloodWait, but keeping a safe pace here means we hit that
# limit far less often in the first place.
BALL_EDIT_DELAY = 1.6


async def _safe_edit_scorecard(session, *, reply_markup=None, bowler_prompt: bool = False) -> None:
    """Best-effort live-scorecard edit. If Telegram still rejects this
    after app.py's own FloodWait retries are exhausted, the match
    state has already moved on regardless - so we log and continue
    instead of letting the whole over/callback die here."""
    try:
        await app.edit_message_text(
            session.chat_id,
            session.live_message_id,
            render_live_scorecard(session, bowler_prompt=bowler_prompt),
            parse_mode="HTML",
            reply_markup=reply_markup if reply_markup is not None else NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[play] Non-fatal scorecard edit failure ignored (match_id={session.match_id}): {exc!r}")


def _team_display(match: dict[str, Any], team_id: int) -> str:
    if int(team_id) == int(match["challenger_id"]):
        return display_name(match.get("challenger_username"), match.get("challenger_name"))
    return display_name(match.get("opponent_username"), match.get("opponent_name"))


def _match_ready_text(match: dict[str, Any]) -> str:
    challenger = display_name(match.get("challenger_username"), match.get("challenger_name"))
    opponent = display_name(match.get("opponent_username"), match.get("opponent_name"))
    toss_winner_id = int(match.get("toss_winner_id") or 0)
    if toss_winner_id == int(match["challenger_id"]):
        toss_winner = challenger
    else:
        toss_winner = opponent
    decision_word = "BAT" if str(match.get("decision") or "").strip().lower() == "bat" else "BOWL"
    return (
        "<b>╭━━〔 🏏 MATCH READY 〕━━╮</b>\n\n"
        "<b>🏆 T20 • 20 Overs</b>\n\n"
        f"<b>🏏 {challenger} XI</b>\n"
        "<b>⚔️</b>\n"
        f"<b>🎯 {opponent} XI</b>\n\n"
        f"<b>{pitch_label(str(match.get('pitch') or 'green'))} Pitch</b>\n"
        f"<b>🏟️ {match.get('stadium')}</b>\n"
        f"<b>🌡️ {match.get('weather')}</b>\n\n"
        f"<b>🪙 Toss ➤ {toss_winner}</b>\n"
        f"<b>🎯 Chose to {decision_word}</b>\n\n"
        "<b>⚡ The field is set.\nThe first ball awaits...</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _send_match_ready(chat_id: int, stadium_name: str, text: str) -> dict:
    """Sends the MATCH READY card exactly as built by _match_ready_text()
    - this never changes the message content, only whether it's sent as
    a plain message or as a photo with that same text as the caption.

    Cache-first: a stadium's image URL is only ever searched for once.
    Every later match at the same stadium reuses the saved Telegram
    file_id, no search needed. On a cache miss, the found URL is handed
    straight to Telegram's send_photo - Telegram's own servers fetch
    it, we never download it ourselves.
    """
    cached_file_id = await get_stadium_image(stadium_name)
    if cached_file_id:
        try:
            return await app.send_photo(chat_id, photo=cached_file_id, caption=text, parse_mode="HTML")
        except Exception as exc:
            print(f"[play] Cached stadium photo failed to send, falling back to search: {exc!r}")

    image_url = await find_stadium_image_url(stadium_name)
    if image_url:
        try:
            sent = await app.send_photo(chat_id, photo=image_url, caption=text, parse_mode="HTML")
            file_id = (sent.get("photo") or {}).get("file_id")
            if file_id:
                await save_stadium_image(stadium_name, file_id)
            return sent
        except Exception as exc:
            print(f"[play] Telegram couldn't fetch the stadium photo URL, falling back to text: {exc!r}")

    return await app.send_message(chat_id, text, parse_mode="HTML")


async def begin_match_flow(chat_id: int, match: dict[str, Any]) -> None:
    await asyncio.sleep(1.5)

    match = dict(match)
    match["stadium"] = random_stadium()
    match["weather"] = random_weather().format()

    challenger_id = int(match["challenger_id"])
    opponent_id = int(match["opponent_id"])
    toss_winner_id = int(match.get("toss_winner_id") or 0)
    decision = str(match.get("decision") or "").strip().lower()

    if decision == "bat":
        batting_team_id = toss_winner_id
        bowling_team_id = opponent_id if batting_team_id == challenger_id else challenger_id
    else:
        bowling_team_id = toss_winner_id
        batting_team_id = opponent_id if bowling_team_id == challenger_id else challenger_id

    batting_squad = await load_current_xi(batting_team_id) or []
    bowling_squad = await load_current_xi(bowling_team_id) or []

    batting_display = _team_display(match, batting_team_id)
    bowling_display = _team_display(match, bowling_team_id)

    session = create_play_session(
        match_id=int(match["match_id"]),
        chat_id=chat_id,
        match=match,
        pitch=str(match.get("pitch") or "green"),
        stadium=match["stadium"],
        weather=match["weather"],
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        batting_team_display=batting_display,
        bowling_team_display=bowling_display,
        batting_squad=batting_squad,
        bowling_squad=bowling_squad,
    )
    start_new_partnership(session)

    ready = await _send_match_ready(chat_id, match["stadium"], _match_ready_text(match))
    session.ready_message_id = ready["message_id"]
    try:
        await app.pin_chat_message(chat_id, ready["message_id"], disable_notification=True)
    except Exception as exc:
        print(f"[play] Failed to pin MATCH READY message: {exc!r}")

    await asyncio.sleep(1)
    live = await app.send_message(
        chat_id,
        render_live_scorecard(session, bowler_prompt=True),
        parse_mode="HTML",
        reply_markup=bowler_selection_keyboard(match["match_id"], next_bowler_card(session)),
    )
    session.live_message_id = live["message_id"]


@register_callback("play_bowler")
async def on_play_bowler(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid bowler selection.", show_alert=True)
        return
    _, match_id_str, player_id_str = parts
    match_id = int(match_id_str)
    player_id = int(player_id_str)
    session = get_session(match_id)
    if session is None:
        await app.answer_callback_query(callback_query["id"], "This match session is unavailable.", show_alert=True)
        return

    presser = callback_query["from"]
    if int(presser["id"]) != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query["id"], "Only the bowling side can choose the bowler.", show_alert=True)
        return

    candidate = None
    for player in next_bowler_card(session):
        if int(player.get("player_id") or 0) == player_id:
            candidate = player
            break
    if candidate is None:
        await app.answer_callback_query(callback_query["id"], "That bowler isn't available.", show_alert=True)
        return

    if session.current_bowler is not None:
        await app.answer_callback_query(callback_query["id"], "⚠️ Bowler already chosen.", show_alert=True)
        return

    if not assign_bowler(session, candidate):
        await app.answer_callback_query(callback_query["id"], "That bowler has no overs left.", show_alert=True)
        return
    session.this_over = []
    await app.answer_callback_query(callback_query["id"], f"Bowler set to {candidate.get('name')}!")
    await _safe_edit_scorecard(session, reply_markup=bowler_tactic_keyboard(match_id, session.current_bowler))


@register_callback("play_tactic")
async def on_play_tactic(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid tactic.", show_alert=True)
        return
    _, match_id_str, tactic = parts
    match_id = int(match_id_str)
    session = get_session(match_id)
    if session is None:
        await app.answer_callback_query(callback_query["id"], "This match session is unavailable.", show_alert=True)
        return

    presser = callback_query["from"]
    if int(presser["id"]) != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query["id"], "Only the bowling side can choose the tactic.", show_alert=True)
        return
    if session.current_bowler is None:
        await app.answer_callback_query(callback_query["id"], "Choose a bowler first.", show_alert=True)
        return
    if session.current_tactic is not None:
        await app.answer_callback_query(callback_query["id"], "⚠️ Bowling tactic already chosen.", show_alert=True)
        return

    assign_tactic(session, tactic)
    await app.answer_callback_query(callback_query["id"], f"{tactic.replace('_', ' ').upper()} tactic set!")
    await _safe_edit_scorecard(session, reply_markup=strategy_keyboard(match_id))


def _innings_break_text(innings_1: dict) -> str:
    bats = top_batters(innings_1)
    bowls = top_bowlers(innings_1)
    bat_lines = "\n".join(f"{i + 1}. {b['name']} - {b['runs']} ({b['balls']})" for i, b in enumerate(bats)) or "-"
    bowl_lines = "\n".join(f"{i + 1}. {b['name']} - {b['wickets']}W ({b['runs']}R)" for i, b in enumerate(bowls)) or "-"
    target = innings_1["runs"] + 1
    return (
        "<b>╭━━〔 🏁 INNINGS BREAK 〕━━╮\n\n"
        f"🏏 {innings_1['batting_team_display']} Innings\n"
        f"📊 {innings_1['runs']}/{innings_1['wickets']} ({innings_1['over_text']} Ov)\n\n"
        f"⭐ Top Batters\n{bat_lines}\n\n"
        f"🎯 Top Bowlers\n{bowl_lines}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Target: {target} for {innings_1['bowling_team_display']} XI in 120 balls\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _match_result_text(innings_1: dict, innings_2: dict) -> str:
    winner_id, margin = match_winner(innings_1, innings_2)
    if winner_id is None:
        headline = "🤝 Match Tied!"
    else:
        winner_display = innings_1["batting_team_display"] if winner_id == innings_1["batting_team_id"] else innings_2["batting_team_display"]
        headline = f"🎉 {winner_display} XI won {margin}!"

    def _innings_block(snap: dict) -> str:
        bats = top_batters(snap)
        bowls = top_bowlers(snap)
        bat_lines = "\n".join(f"⭐ {b['name']} - {b['runs']} ({b['balls']})" for b in bats) or "⭐ -"
        bowl_lines = "\n".join(f"🎯 {b['name']} - {b['wickets']}W ({b['runs']}R)" for b in bowls) or "🎯 -"
        return (
            f"🏏 {snap['batting_team_display']} Innings — {snap['runs']}/{snap['wickets']} ({snap['over_text']} Ov)\n"
            f"{bat_lines}\n{bowl_lines}"
        )

    potm = player_of_the_match(innings_1, innings_2)

    return (
        "<b>╭━━〔 🏆 MATCH RESULT 〕━━╮\n\n"
        f"{headline}\n\n"
        "📋 MATCH HIGHLIGHTS\n\n"
        f"{_innings_block(innings_1)}\n\n"
        f"{_innings_block(innings_2)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌟 Player of the Match: {potm}\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )



def _exit_match_result_text(session, exiter_mention: str, winner_mention: str) -> str:
    """Build the same MATCH RESULT/HIGHLIGHTS style for an early exit,
    using only innings that actually happened up to the exit point."""
    snapshots = list(session.innings_history)
    current_snapshot = snapshot_innings(session)
    if not snapshots or snapshots[-1].get("innings_number") != current_snapshot.get("innings_number"):
        snapshots.append(current_snapshot)
    potm = "Player"
    if snapshots:
        best_score = -1.0
        for snap in snapshots:
            for batter in snap.get("batters", []):
                impact = int(batter.get("runs") or 0)
                if impact > best_score:
                    best_score = impact
                    potm = batter.get("name") or "Player"
            for bowler in snap.get("bowlers", []):
                impact = int(bowler.get("wickets") or 0) * 25 - int(bowler.get("runs") or 0) * 0.2
                if impact > best_score:
                    best_score = impact
                    potm = bowler.get("name") or "Player"

    def _innings_block(snap: dict) -> str:
        bats = top_batters(snap)
        bowls = top_bowlers(snap)
        bat_lines = "\n".join(f"⭐ {b['name']} - {b['runs']} ({b['balls']})" for b in bats) or "⭐ -"
        bowl_lines = "\n".join(f"🎯 {b['name']} - {b['wickets']}W ({b['runs']}R)" for b in bowls) or "🎯 -"
        return (
            f"🏏 {snap['batting_team_display']} Innings — {snap['runs']}/{snap['wickets']} ({snap['over_text']} Ov)\n"
            f"{bat_lines}\n{bowl_lines}"
        )

    blocks = "\n\n".join(_innings_block(s) for s in snapshots)
    return (
        "<b>╭━━〔 🏆 MATCH RESULT 〕━━╮\n\n"
        f"🎉 {winner_mention} XI won by opponent exit!\n\n"
        "📋 MATCH HIGHLIGHTS\n\n"
        f"{blocks}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🚪 {exiter_mention} exited the game.\n\n"
        f"🌟 Player of the Match: {html_escape_simple(potm)}\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def html_escape_simple(value: str) -> str:
    from html import escape
    return escape(str(value or "Player"))


async def _safe_send(chat_id, text, **kwargs):
    try:
        return await app.send_message(chat_id, text, **kwargs)
    except Exception as exc:
        print(f"[play] Non-fatal send_message failure ignored (chat_id={chat_id}): {exc!r}")
        return {}


async def _record_player_squad_stats(session, innings_1: dict, innings_2: dict) -> None:
    """Persist per-user player stats for the current match.

    The shared helper is idempotent and also works for special-edition player
    IDs, so one special player can no longer roll back the whole match ledger.
    """
    try:
        await record_session_player_stats(session)
    except Exception as exc:
        print(f"[play] Failed to persist per-player squad stats: {exc!r}")

async def _award_match_xp_and_stats(session, innings_1: dict, innings_2: dict) -> None:
    """Awards level XP and updates win/loss stats for both players once a
    match is fully decided. Best-effort - a failure here must never stop
    the match-result message from being shown."""
    match = session.match
    challenger_id = match["challenger_id"]
    opponent_id = match["opponent_id"]
    winner_id, _margin = match_winner(innings_1, innings_2)

    try:
        if winner_id is None:
            # Tied match: both players get the tie XP, no win/loss recorded.
            await add_match_xp(challenger_id, TIE_XP)
            await add_match_xp(opponent_id, TIE_XP)
            await record_match_result(challenger_id, won=None)
            await record_match_result(opponent_id, won=None)
        else:
            loser_id = opponent_id if int(winner_id) == int(challenger_id) else challenger_id
            await award_competitive_rewards(winner_id, loser_id)
    except Exception as exc:
        print(f"[play] Failed to award match XP/stats for match_id={session.match_id}: {exc!r}")


async def _finish_over_and_prompt_next(session) -> None:
    # The live scorecard message itself becomes the short over summary.
    # Keep it visible for exactly a brief moment, then remove it and create
    # the fresh live scorecard with the next bowler choices.
    summary = over_complete_text(session)
    try:
        await app.edit_message_text(
            session.chat_id,
            session.live_message_id,
            summary,
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[play] Failed to show over-complete card: {exc!r}")

    await asyncio.sleep(3)

    if session.live_message_id:
        try:
            await app.delete_message(session.chat_id, session.live_message_id)
        except Exception as exc:
            print(f"[play] Failed to delete over-complete card: {exc!r}")
        session.live_message_id = None

    if innings_completed(session):
        if session.innings.innings_number == 1:
            innings_1_snapshot = snapshot_innings(session)
            session.innings_history.append(innings_1_snapshot)

            await _safe_send(session.chat_id, _innings_break_text(innings_1_snapshot), parse_mode="HTML")

            target = innings_1_snapshot["runs"] + 1
            start_second_innings(session, target)
            start_new_partnership(session)

            await asyncio.sleep(1.5)
            live = await _safe_send(
                session.chat_id,
                render_live_scorecard(session, bowler_prompt=True),
                parse_mode="HTML",
                reply_markup=bowler_selection_keyboard(session.match_id, next_bowler_card(session)),
            )
            if live.get("message_id"):
                session.live_message_id = live["message_id"]
            return

        # Second (or later) innings just finished - the match is over.
        innings_2_snapshot = snapshot_innings(session)
        innings_1_snapshot = session.innings_history[0] if session.innings_history else innings_2_snapshot
        winner_id, margin = match_winner(innings_1_snapshot, innings_2_snapshot)
        winner = (innings_1_snapshot["batting_team_display"] if winner_id == innings_1_snapshot["batting_team_id"]
                  else innings_2_snapshot["batting_team_display"]) if winner_id is not None else "MATCH TIED"
        potm_name = player_of_the_match(innings_1_snapshot, innings_2_snapshot)
        match_result_text = _match_result_text(innings_1_snapshot, innings_2_snapshot)
        await _safe_send(session.chat_id, match_result_text, parse_mode="HTML")
        await _record_player_squad_stats(session, innings_1_snapshot, innings_2_snapshot)
        await _award_match_xp_and_stats(session, innings_1_snapshot, innings_2_snapshot)
        match = session.match
        challenger_id = int(match.get("challenger_id") or 0)
        opponent_id = int(match.get("opponent_id") or 0)
        try:
            await send_match_summary(
                app, session.chat_id, [innings_1_snapshot, innings_2_snapshot],
                winner=winner, margin=margin, potm=player_details(
                    [innings_1_snapshot, innings_2_snapshot], potm_name),
                caption=(
                    build_result_caption(
                        mention_html(
                            int(winner_id),
                            match.get("challenger_username") if int(winner_id) == int(match.get("challenger_id") or 0) else match.get("opponent_username"),
                            match.get("challenger_name") if int(winner_id) == int(match.get("challenger_id") or 0) else match.get("opponent_name"),
                        ),
                        mention_html(
                            int(opponent_id if int(winner_id) == int(challenger_id) else challenger_id),
                            match.get("opponent_username") if int(winner_id) == int(challenger_id) else match.get("challenger_username"),
                            match.get("opponent_name") if int(winner_id) == int(challenger_id) else match.get("challenger_name"),
                        ),
                    )
                    if winner_id is not None else "<b>🏏 MATCH REWARDS</b>\n\nMatch tied. No winner/loser reward applied."
                ),
            )
        except Exception as exc:
            print(f"[play] Summary card failed after match-result text was sent: {exc!r}")
        try:
            await update_status(session.match_id, "completed")
        except Exception as exc:
            print(f"[play] Failed to mark match_id={session.match_id} completed: {exc!r}")
        try:
            from services.match_notification import send_match_completion_notification
            await send_match_completion_notification(
                app, engine="PLAY", pitch=session.match.get("pitch"),
                user1=(session.match.get("challenger_username"), session.match.get("challenger_name")),
                user2=(session.match.get("opponent_username"), session.match.get("opponent_name")),
                innings_1=innings_1_snapshot, innings_2=innings_2_snapshot,
                result=match_result_text,
            )
        except Exception as exc:
            print(f"[play] Match notification failed: {exc!r}")
        clear_session(session.match_id)
        return

    # Carry the completed over forward into the next live card so the
    # newly displayed bowler-selection message keeps the previous over
    # timeline and its six-ball commentary instead of resetting to "-".
    session.last_over = list(session.this_over)
    session.last_over_commentary = list(session.over_commentary)

    session.current_bowler = None
    session.current_strategy = None
    session.current_tactic = None
    session.stage = "choose_bowler"
    session.this_over = []
    session.over_commentary = []

    live = await _safe_send(
        session.chat_id,
        render_live_scorecard(session, bowler_prompt=True),
        parse_mode="HTML",
        reply_markup=bowler_selection_keyboard(session.match_id, next_bowler_card(session)),
    )
    if live.get("message_id"):
        session.live_message_id = live["message_id"]


@register_callback("play_strategy")
async def on_play_strategy(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid strategy.", show_alert=True)
        return
    _, match_id_str, strategy = parts
    match_id = int(match_id_str)
    session = get_session(match_id)
    if session is None:
        await app.answer_callback_query(callback_query["id"], "This match session is unavailable.", show_alert=True)
        return

    presser = callback_query["from"]
    if int(presser["id"]) != int(session.batting_team_id):
        await app.answer_callback_query(callback_query["id"], "Only the batting side can choose the approach.", show_alert=True)
        return
    if session.current_bowler is None:
        await app.answer_callback_query(callback_query["id"], "Choose a bowler first.", show_alert=True)
        return
    if session.current_tactic is None:
        await app.answer_callback_query(callback_query["id"], "Waiting on the bowling tactic first.", show_alert=True)
        return
    if session.current_strategy is not None or session.stage != "choose_strategy":
        await app.answer_callback_query(callback_query["id"], "⚠️ Batting approach already chosen.", show_alert=True)
        return

    session.current_strategy = strategy
    session.stage = "over_in_progress"
    await app.answer_callback_query(callback_query["id"], f"{strategy.upper()} selected!")

    # Remove the approach buttons immediately, then simulate the whole over
    # internally. No ball-by-ball scorecard edits are shown to the users.
    await _safe_edit_scorecard(session)

    legal_balls_before = session.innings.score.legal_balls
    while not session.innings.completed and (session.innings.score.legal_balls - legal_balls_before) < 6:
        simulate_ball(session, strategy)

    await _finish_over_and_prompt_next(session)
