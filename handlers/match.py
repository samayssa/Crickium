from __future__ import annotations

print("match.py loaded")

import asyncio
from typing import Any

from handlers.registry import register, register_callback
from app import app
from utils.mentions import mention
from utils.timers import start_timer, cancel_timer
from buttons.challenge_buttons import accept_decline_keyboard
from buttons.toss_buttons import heads_tails_keyboard
from buttons.decision_buttons import bat_bowl_keyboard
from buttons.lineup_buttons import player_selection_keyboard
from buttons.delivery_buttons import delivery_type_keyboard, line_keyboard, length_keyboard
from buttons.shot_buttons import foot_movement_keyboard, stroke_type_keyboard, shot_keyboard
from engines.match_engine import (
    MATCH_ENGINE,
    resolve_toss,
    challenge_message,
    challenge_expired_message,
    challenge_declined_message,
    match_confirmed_message,
    toss_time_message,
    toss_result_message,
    match_starting_message,
    reminder_message,
)
from engines.score_engine import create_score_state
from engines.commentary_engine import innings_summary, match_result_message
from engines.lineup_engine import (
    load_squad,
    find_player_by_id,
    reorder_openers,
    bowling_candidates,
    render_opening_selection_message,
    render_bowler_selection_message,
    render_ready_message,
)
from database.query import execute, fetchrow
from database.challenges_repo import (
    create_challenge, set_message_id, get_challenge, update_status,
    set_opponent_id, set_toss, set_decision,
)
from database.squads_repo import get_team_squad
from utils.debut_gate import has_minimum_team, get_playing_xi_status
from engines.innings_engine import create_innings
from services.match_summary import send_match_summary, snapshot_normal_session, player_details, best_player

NO_KEYBOARD = {"inline_keyboard": []}


def _short_symbol(outcome: str, runs: int, wicket: bool, extra_type: str | None = None) -> str:
    if wicket:
        return "W"
    if extra_type == "wide":
        return "wd"
    if extra_type == "no_ball":
        return "nb"
    if outcome == "six":
        return "6"
    if outcome in {"four", "boundary"}:
        return "4"
    if runs == 0:
        return "•"
    return str(runs)


def _update_ball_stats(session, batter_name: str, bowler_name: str, outcome: str, runs: int, wicket: bool, legal_delivery: bool, extra_type: str | None = None):
    batter_stats = session.meta.setdefault("batter_stats", {})
    b = batter_stats.setdefault(batter_name, {"runs": 0, "balls": 0, "fours": 0, "sixes": 0})
    if legal_delivery:
        b["balls"] += 1
    b["runs"] += runs
    if outcome in {"four", "boundary"}:
        b["fours"] += 1
    if outcome == "six":
        b["sixes"] += 1

    bowler_stats = session.meta.setdefault("bowler_stats", {})
    bs = bowler_stats.setdefault(bowler_name, {"balls": 0, "runs": 0, "wickets": 0})
    if legal_delivery:
        bs["balls"] += 1
    bs["runs"] += runs
    if wicket:
        bs["wickets"] += 1

    over_events = session.meta.setdefault("current_over_events", [])
    over_events.append(_short_symbol(outcome, runs, wicket, extra_type))


def _format_overs_from_balls(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}"


def _live_scorecard_text(session) -> str:
    batting_display = session.meta.get("batting_display") or "Batting Side"
    bowling_display = session.meta.get("bowling_display") or "Bowling Side"
    innings = session.innings
    if innings is None:
        return "*🏏 LIVE SCORECARD*\n\n_Match starting..._"

    score_line = f"{innings.score.runs}/{innings.score.wickets}  ({innings.score.over_text} ov)"
    striker = innings.striker.name if innings.striker else "Player"
    non_striker = innings.non_striker.name if innings.non_striker else "Player"
    batter_stats = session.meta.get("batter_stats", {})
    s_stats = batter_stats.get(striker, {"runs": 0, "balls": 0})
    ns_stats = batter_stats.get(non_striker, {"runs": 0, "balls": 0})
    bowler_name = session.current_bowler.get("name") if session.current_bowler else "Bowler"
    bowler_stats = session.meta.get("bowler_stats", {}).get(bowler_name, {"balls": 0, "runs": 0, "wickets": 0})
    bowler_overs = _format_overs_from_balls(bowler_stats["balls"])
    over_events = session.meta.get("current_over_events", [])

    target_line = ""
    if innings.target:
        balls_left = max(0, 120 - innings.score.legal_balls)
        need = max(0, innings.target - innings.score.runs)
        target_line = f"\n*🎯 Target:* {innings.target}  |  Need *{need}* off *{balls_left}* balls"

    lines = [
        "*🏏 LIVE SCORECARD*",
        "",
        f"*👤 Batting:* {batting_display}",
        f"*Score:* {score_line}{target_line}",
        "",
        f"*{striker} 🏏* — {s_stats['runs']}({s_stats['balls']})",
        f"*{non_striker}* — {ns_stats['runs']}({ns_stats['balls']})",
        "",
        f"*🎯 Bowling:* {bowling_display}",
        f"*{bowler_name}* — {bowler_stats['wickets']}/{bowler_stats['runs']} ({bowler_overs} ov)",
        "",
        f"*This over:* {'  '.join(over_events) if over_events else '—'}",
    ]
    return "\n".join(lines)


def _over_summary_text(session) -> str:
    innings = session.innings
    over_events = session.meta.get("current_over_events", [])
    bowler_name = session.current_bowler.get("name") if session.current_bowler else "Bowler"
    over_number = max(1, innings.score.legal_balls // 6)
    return "\n".join([
        "*📖 OVER SUMMARY*",
        "",
        f"*Over {over_number}:* {bowler_name}",
        f"*Balls:* {'  '.join(over_events) if over_events else '—'}",
        "",
        f"*Score:* {innings.score.runs}/{innings.score.wickets}  ({innings.score.over_text} ov)",
    ])


async def _start_second_innings(chat_id: int, challenge: dict[str, Any], session):
    session.meta["first_innings_card_snapshot"] = snapshot_normal_session(session)
    first_innings_summary = innings_summary(
        session.meta.get("batting_display"),
        session.innings.score.runs,
        session.innings.score.wickets,
        session.innings.score.over_text,
    )
    target = session.innings.score.runs + 1

    new_batting_team_id = session.meta["bowling_team_id"]
    new_bowling_team_id = session.meta["batting_team_id"]
    new_batting_display = session.meta["bowling_display"]
    new_bowling_display = session.meta["batting_display"]

    batting_squad = await get_team_squad(new_batting_team_id) or []
    bowling_squad = await get_team_squad(new_bowling_team_id) or []
    if not batting_squad or not bowling_squad:
        await app.send_message(chat_id, "⚠️ Could not start the second innings: squad missing.")
        return

    session.meta["batting_team_id"] = new_batting_team_id
    session.meta["bowling_team_id"] = new_bowling_team_id
    session.meta["batting_display"] = new_batting_display
    session.meta["bowling_display"] = new_bowling_display
    session.meta["batting_squad"] = batting_squad
    session.meta["bowling_squad"] = bowling_squad
    session.meta["selected_striker_id"] = None
    session.meta["selected_non_striker_id"] = None
    session.meta["selected_bowler_id"] = None
    session.meta["lineup_bowling_started"] = False
    session.meta["match_playing_started"] = False
    session.meta["batter_stats"] = {}
    session.meta["bowler_stats"] = {}
    session.meta["current_over_events"] = []
    session.meta["first_innings_summary"] = first_innings_summary

    order = [
        {"name": p.get("name"), "role": p.get("role"), "bat_level": p.get("bat_level"), "bowl_level": p.get("bowl_level"), "player_id": p.get("player_id")}
        for p in batting_squad
    ]
    new_innings = create_innings(
        batting_team_id=new_batting_team_id,
        bowling_team_id=new_bowling_team_id,
        batting_order=order,
        innings_number=2,
        target=target,
    )
    session.innings = new_innings
    session.score = create_score_state()
    session.current_batsman = None
    session.current_bowler = None
    session.balls_faced = 0
    session.batting_order = []
    session.bowling_order = []
    session.stage = "lineup"

    text = (
        f"*🔄 INNINGS BREAK*\n\n"
        f"{first_innings_summary}\n\n"
        f"*🎯 Target:* {target}\n\n"
        f"*👤 Now Batting:* {new_batting_display}\n"
        f"*🎳 Now Bowling:* {new_bowling_display}\n\n"
        f"*Second innings begins now!*"
    )
    await app.send_message(chat_id, text, parse_mode="Markdown")

    selection_text = render_opening_selection_message(new_batting_display, new_bowling_display, batting_squad)
    sent = await app.send_message(
        chat_id,
        selection_text,
        parse_mode="Markdown",
        reply_markup=player_selection_keyboard("lineup_bat", challenge["challenge_id"], batting_squad, selected_ids=set()),
    )
    session.meta["lineup_message_id"] = sent["message_id"]
    print(f"[match] Second innings lineup selection started for challenge_id={challenge['challenge_id']}, message_id={sent['message_id']}")


async def _finish_match(chat_id: int, challenge: dict[str, Any], session):
    innings = session.innings
    target = innings.target
    batting_display = session.meta.get("batting_display")
    bowling_display = session.meta.get("bowling_display")

    second_summary = innings_summary(batting_display, innings.score.runs, innings.score.wickets, innings.score.over_text, target=target)

    if target is not None and innings.score.runs >= target:
        winner_name, loser_name = batting_display, bowling_display
        margin = f"{max(0, 10 - innings.score.wickets)} wicket(s)"
    elif target is not None and innings.score.runs < target - 1:
        winner_name, loser_name = bowling_display, batting_display
        margin = f"{target - 1 - innings.score.runs} run(s)"
    elif target is not None and innings.score.runs == target - 1:
        winner_name, loser_name, margin = None, None, "a tie"
    else:
        winner_name, loser_name, margin = batting_display, bowling_display, "the result"

    result_text = match_result_message(winner_name, loser_name, margin)
    first_summary = session.meta.get("first_innings_summary", "")

    text = (
        f"*🏆 MATCH RESULT*\n\n"
        f"{first_summary}\n"
        f"{second_summary}\n\n"
        f"*{result_text}*"
    )
    first_card = session.meta.get("first_innings_card_snapshot")
    second_card = snapshot_normal_session(session)
    # Preserve the existing text result as the first post-match message. The
    # image summary follows it immediately.
    await app.send_message(chat_id, text, parse_mode="Markdown")
    try:
        cards = [first_card, second_card] if first_card else [second_card, second_card]
        potm_name = best_player(cards)
        await send_match_summary(
            app, chat_id, cards, winner=winner_name or "MATCH TIED",
            margin=margin, potm=player_details(cards, potm_name),
        )
    except Exception as exc:
        print(f"[match] Summary card failed after match-result text was sent: {exc!r}")

    try:
        await update_status(challenge["challenge_id"], "completed")
    except Exception as exc:
        print(f"[match] Failed to update challenge status to completed: {exc!r}")

    MATCH_ENGINE.clear_session(chat_id)
    print(f"[match] Match finished for challenge_id={challenge['challenge_id']}. {result_text}")


def parse_username_arg(text: str) -> str | None:
    parts = text.split()
    for part in parts[1:]:
        if part.startswith("@"):
            return part[1:]
    return None


async def ensure_user(user_id, username, first_name):
    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, last_seen_at = NOW();
        """,
        user_id, username, first_name,
    )


async def safe_answer_callback(callback_query_id: str, text: str | None = None, show_alert: bool = False):
    try:
        await app.answer_callback_query(callback_query_id, text=text, show_alert=show_alert)
    except Exception as exc:
        print(f"[match] answer_callback_query failed but flow continues: {exc!r}")


def _is_team_member_allowed(user_id: int, team_id: int | None) -> bool:
    return team_id is not None and int(user_id) == int(team_id)


async def safe_edit_message_text(chat_id: int, message_id: int, text: str, parse_mode: str | None = None, reply_markup: dict | None = None):
    try:
        return await app.edit_message_text(chat_id, message_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as exc:
        msg = str(exc).lower()
        if "message is not modified" in msg:
            print(f"[match] edit_message_text not modified; ignoring: {exc!r}")
            return {}
        raise


async def _get_session(chat_id: int, challenge: dict[str, Any]):
    session = MATCH_ENGINE.get_session(chat_id)
    if session is None:
        session = MATCH_ENGINE.create_session(
            chat_id=chat_id,
            challenger_id=challenge["challenger_id"],
            opponent_id=challenge["opponent_id"],
            challenger_username=challenge.get("challenger_username"),
            opponent_username=challenge.get("opponent_username"),
            challenger_name=challenge.get("challenger_name"),
            opponent_name=challenge.get("opponent_name"),
            challenge_id=challenge["challenge_id"],
            format_=challenge.get("format") or "T20",
        )
    return session


async def _get_participant_squads(batting_team_id: int, bowling_team_id: int):
    batting_squad = await get_team_squad(batting_team_id)
    bowling_squad = await get_team_squad(bowling_team_id)
    return batting_squad, bowling_squad


async def _start_toss_stage(challenge_id, chat_id, challenger_display):
    text = toss_time_message(challenger_display)
    sent = await app.send_message(chat_id, text, parse_mode="Markdown", reply_markup=heads_tails_keyboard(challenge_id))
    await set_message_id(challenge_id, sent["message_id"])
    print(f"[match] Toss stage started for challenge_id={challenge_id}, message_id={sent['message_id']}")

    challenge = await get_challenge(challenge_id)
    challenger_mention = mention(challenge["challenger_id"], challenge["challenger_username"], challenge["challenger_name"])

    async def on_reminder(remaining):
        await app.send_message(chat_id, reminder_message(challenger_mention, remaining, "make your toss call"), parse_mode="Markdown")

    async def on_timeout():
        print(f"[match] Toss call timed out for challenge_id={challenge_id}, auto-calling 'heads'.")
        current = await get_challenge(challenge_id)
        if current["status"] != "accepted":
            return
        await _resolve_toss_and_advance(challenge_id, chat_id, "heads")

    start_timer("toss_call", challenge_id, on_reminder, on_timeout, total_seconds=90)


async def _start_opening_selection(chat_id: int, challenge: dict[str, Any], decision: str):
    session = await _get_session(chat_id, challenge)
    winner_id = challenge["toss_winner_id"]
    loser_id = challenge["opponent_id"] if winner_id == challenge["challenger_id"] else challenge["challenger_id"]

    if decision == "bat":
        batting_team_id = winner_id
        bowling_team_id = loser_id
    else:
        batting_team_id = loser_id
        bowling_team_id = winner_id

    batting_squad, bowling_squad = await _get_participant_squads(batting_team_id, bowling_team_id)
    if not batting_squad or not bowling_squad:
        await app.send_message(chat_id, "⚠️ One or both squads are missing. Use /debut first so each side has a Playing XI.")
        return
    if len(batting_squad) < 11 or len(bowling_squad) < 11:
        await app.send_message(
            chat_id,
            "<b>⚠️ You need a minimum 11 players team to join this game.</b>",
            parse_mode="HTML",
        )
        return

    batting_team_display = mention(
        batting_team_id,
        challenge["challenger_username"] if batting_team_id == challenge["challenger_id"] else challenge["opponent_username"],
        challenge["challenger_name"] if batting_team_id == challenge["challenger_id"] else challenge["opponent_name"],
    )
    bowling_team_display = mention(
        bowling_team_id,
        challenge["challenger_username"] if bowling_team_id == challenge["challenger_id"] else challenge["opponent_username"],
        challenge["challenger_name"] if bowling_team_id == challenge["challenger_id"] else challenge["opponent_name"],
    )

    session.meta["batting_team_id"] = batting_team_id
    session.meta["bowling_team_id"] = bowling_team_id
    session.meta["batting_display"] = batting_team_display
    session.meta["bowling_display"] = bowling_team_display
    session.meta["batting_squad"] = batting_squad
    session.meta["bowling_squad"] = bowling_squad
    session.meta["selected_striker_id"] = None
    session.meta["selected_non_striker_id"] = None
    session.meta["selected_bowler_id"] = None
    session.stage = "lineup"

    text = render_opening_selection_message(batting_team_display, bowling_team_display, batting_squad)
    sent = await app.send_message(
        chat_id,
        text,
        parse_mode="Markdown",
        reply_markup=player_selection_keyboard("lineup_bat", challenge["challenge_id"], batting_squad, selected_ids=set()),
    )
    session.meta["lineup_message_id"] = sent["message_id"]
    print(f"[match] Opening selection started for challenge_id={challenge['challenge_id']}, message_id={sent['message_id']}")


async def _start_bowler_selection(chat_id: int, challenge: dict[str, Any], batting_team_id: int, bowling_team_id: int):
    session = await _get_session(chat_id, challenge)
    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    bowling_squad = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []
    bowlers = bowling_candidates(bowling_squad)

    batting_team_display = session.meta.get("batting_display") or "Batting Side"
    bowling_team_display = session.meta.get("bowling_display") or "Bowling Side"
    striker = find_player_by_id(batting_squad, int(session.meta.get("selected_striker_id") or 0)) if session.meta.get("selected_striker_id") else None
    non_striker = find_player_by_id(batting_squad, int(session.meta.get("selected_non_striker_id") or 0)) if session.meta.get("selected_non_striker_id") else None

    if not bowlers:
        await app.send_message(chat_id, "⚠️ No bowlers found in the bowling squad.")
        return

    text = render_bowler_selection_message(bowling_team_display, batting_team_display, striker, non_striker)
    sent = await app.send_message(
        chat_id,
        text,
        parse_mode="Markdown",
        reply_markup=player_selection_keyboard("lineup_bowl", challenge["challenge_id"], bowlers),
    )
    session.meta["bowler_message_id"] = sent["message_id"]
    print(f"[match] Bowler selection started for challenge_id={challenge['challenge_id']}, message_id={sent['message_id']}")


async def _finalize_lineup(chat_id: int, challenge: dict[str, Any]):
    session = await _get_session(chat_id, challenge)
    batting_team_id = int(session.meta["batting_team_id"])
    bowling_team_id = int(session.meta["bowling_team_id"])
    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    bowlers = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []

    striker = find_player_by_id(batting_squad, int(session.meta["selected_striker_id"]))
    non_striker = find_player_by_id(batting_squad, int(session.meta["selected_non_striker_id"]))
    bowler = find_player_by_id(bowlers, int(session.meta["selected_bowler_id"]))

    if striker is None or non_striker is None or bowler is None:
        await app.send_message(chat_id, "⚠️ The lineup is incomplete.")
        return

    batting_order = reorder_openers(batting_squad, int(striker["player_id"]), int(non_striker["player_id"]))
    MATCH_ENGINE.set_batting_order(session, batting_order)
    session.innings = create_innings(
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        batting_order=batting_order,
        innings_number=1,
    )
    session.current_batsman = striker
    session.current_bowler = bowler
    MATCH_ENGINE.set_bowling_order(session, [bowler] + [p for p in bowlers if int(p.get("player_id") or 0) != int(bowler["player_id"])])
    session.stage = "playing"
    session.meta["match_playing_started"] = True

    batting_display = session.meta.get("batting_display") or "Batting Side"
    bowling_display = session.meta.get("bowling_display") or "Bowling Side"

    try:
        await app.send_message(
            chat_id,
            render_ready_message(batting_display, bowling_display, striker, non_striker, bowler),
            parse_mode="Markdown",
        )
    except Exception as exc:
        print(f"[match] Ready message failed, using fallback text: {exc!r}")
        fallback_text = (
            f"🏟 LINEUP LOCKED\n\n"
            f"Batting Side: {batting_display}\n"
            f"Bowling Side: {bowling_display}\n\n"
            f"Striker: {striker.get('name')}\n"
            f"Non-striker: {non_striker.get('name')}\n"
            f"Bowler: {bowler.get('name')}\n\n"
            f"The next ball can begin now."
        )
        await app.send_message(chat_id, fallback_text)

    await asyncio.sleep(1)
    try:
        await _start_delivery_stage(chat_id, challenge, session)
    except Exception as exc:
        print(f"[match] Delivery stage failed, using fallback screen: {exc!r}")
        session.stage = "delivery_type"
        MATCH_ENGINE.reset_ball_plan(session)
        batting_team_id = int(session.meta["batting_team_id"])
        bowling_team_id = int(session.meta["bowling_team_id"])
        batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
        bowling_squad = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []
        bowler = session.current_bowler or find_player_by_id(bowling_squad, int(session.meta.get("selected_bowler_id") or 0))
        striker = find_player_by_id(batting_squad, int(session.meta.get("selected_striker_id") or 0))
        non_striker = find_player_by_id(batting_squad, int(session.meta.get("selected_non_striker_id") or 0))
        message = [
            "*🎳 DELIVERY SETUP*",
            "",
            f"*Bowling Side:* {session.meta.get('bowling_display') or 'Bowling Side'}",
            f"*Batting Side:* {session.meta.get('batting_display') or 'Batting Side'}",
            "",
            f"*Bowler:* {bowler['name'] if bowler else 'Bowler'}",
            f"*Striker:* {striker['name'] if striker else 'Player'}",
            f"*Non-striker:* {non_striker['name'] if non_striker else 'Player'}",
            "",
            "*Bowler, choose your delivery type.*",
            "*Then set line and length.*",
        ]
        sent = await app.send_message(
            chat_id,
            "\n".join(message),
            parse_mode="Markdown",
            reply_markup=delivery_type_keyboard(challenge["challenge_id"]),
        )
        session.meta["delivery_message_id"] = sent["message_id"]
        print(f"[match] Fallback delivery stage started for challenge_id={challenge['challenge_id']}, message_id={sent['message_id']}")


def parse_username_arg(text: str) -> str | None:
    parts = text.split()
    for part in parts[1:]:
        if part.startswith("@"):
            return part[1:]
    return None


@register("match")
async def match_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    challenger_id = from_user.get("id")
    challenger_username = from_user.get("username")
    challenger_name = from_user.get("first_name", "Player")

    print(f"[match] /match invoked by user_id={challenger_id} in chat_id={chat_id}")
    await ensure_user(challenger_id, challenger_username, challenger_name)

    if not await has_minimum_team(int(challenger_id)):
        await app.send_message(
            chat_id,
            "<b>⚠️ You need a minimum 11 players team to challenge.</b>",
            parse_mode="HTML",
        )
        return

    xi_ok, _xi_reason = await get_playing_xi_status(int(challenger_id))
    if not xi_ok:
        await app.send_message(
            chat_id,
            "<b>⚠️ Your Playing XI is not perfect to challenge this game.</b>\n\n"
            "You need min 3 batsman, 1 wicket-keeper, 3 all-rounder and 3 bowlers.",
            parse_mode="HTML",
        )
        return

    reply_to = message.get("reply_to_message")
    opponent_id = None
    opponent_username = None
    opponent_name = None

    if reply_to and "from" in reply_to:
        opp = reply_to["from"]
        if opp.get("is_bot"):
            await app.send_message(chat_id, "🚫 You can't challenge a bot.")
            return
        opponent_id = opp.get("id")
        opponent_username = opp.get("username")
        opponent_name = opp.get("first_name", "Player")
        print(f"[match] Opponent resolved via reply: id={opponent_id} username=@{opponent_username}")
    else:
        username_arg = parse_username_arg(message.get("text", ""))
        if not username_arg:
            await app.send_message(
                chat_id,
                "⚠️ To challenge someone, either:\n"
                "• Reply to their message with /match, or\n"
                "• Use /match @username"
            )
            return

        opponent_username = username_arg
        row = await fetchrow("SELECT user_id, first_name FROM users WHERE username = $1;", username_arg)
        if row:
            opponent_id = row["user_id"]
            opponent_name = row["first_name"]
            print(f"[match] Opponent resolved via username lookup in DB: id={opponent_id}")
        else:
            opponent_name = username_arg
            print(f"[match] Opponent @{username_arg} not found in DB yet - will validate by username at accept time.")

    if opponent_id == challenger_id:
        await app.send_message(chat_id, "🚫 You can't challenge yourself!")
        return

    if opponent_id is not None and not await has_minimum_team(int(opponent_id)):
        await app.send_message(
            chat_id,
            "<b>⚠️ You need a minimum 11 players team to join this game.</b>",
            parse_mode="HTML",
        )
        return

    challenge = await create_challenge(
        chat_id=chat_id,
        challenger_id=challenger_id,
        challenger_username=challenger_username,
        challenger_name=challenger_name,
        opponent_id=opponent_id,
        opponent_username=opponent_username,
        opponent_name=opponent_name,
        format_="T20",
        expires_in_seconds=60,
    )
    challenge_id = challenge["challenge_id"]
    print(f"[match] Created challenge_id={challenge_id}: {challenger_name} -> {opponent_username or opponent_name}")

    challenger_display = mention(challenger_id, challenger_username, challenger_name)
    opponent_display = mention(opponent_id or challenger_id, opponent_username, opponent_name)

    text = challenge_message(challenger_display, opponent_display, "T20", 60)
    sent = await app.send_message(chat_id, text, parse_mode="Markdown", reply_markup=accept_decline_keyboard(challenge_id))
    await set_message_id(challenge_id, sent["message_id"])
    print(f"[match] Challenge message sent, message_id={sent['message_id']}")

    async def on_reminder(remaining):
        return None

    async def on_timeout():
        print(f"[match] Challenge {challenge_id} expired (no response in 60s).")
        current = await get_challenge(challenge_id)
        if current["status"] != "pending":
            return
        await update_status(challenge_id, "expired")
        await app.edit_message_text(chat_id, sent["message_id"], challenge_expired_message(challenger_display, opponent_display), parse_mode="Markdown", reply_markup=NO_KEYBOARD)

    start_timer("challenge", challenge_id, on_reminder, on_timeout, total_seconds=60)


# Telegram sometimes delivers two callback queries for what is effectively the
# same button press (a slow response tempts the player into tapping again, or
# the client itself retries). Every callback below mutates the shared,
# in-memory MatchSession for a challenge, and those two updates can get
# scheduled concurrently by main.py's asyncio.create_task(...) - without any
# serialization, the second one can read the session before the first one has
# finished writing to it (e.g. selecting a non-striker, then racing itself and
# reverting the pick). Wrapping each handler so only one runs at a time per
# challenge_id fixes that without touching the surrounding game logic.
_match_locks: dict[int, "asyncio.Lock"] = {}


def _get_match_lock(challenge_id: int) -> asyncio.Lock:
    lock = _match_locks.get(challenge_id)
    if lock is None:
        lock = asyncio.Lock()
        _match_locks[challenge_id] = lock
    return lock


def serialize_by_challenge(handler):
    """Decorator for callback handlers whose data is 'action:challenge_id:...'.
    Ensures only one update for a given challenge_id is processed at a time."""
    async def wrapper(callback_query):
        try:
            challenge_id = int(callback_query["data"].split(":")[1])
        except (IndexError, ValueError):
            await handler(callback_query)
            return
        async with _get_match_lock(challenge_id):
            await handler(callback_query)
    wrapper.__name__ = handler.__name__
    return wrapper


def _presser_matches_opponent(challenge, presser_id, presser_username) -> bool:
    if challenge["opponent_id"] is not None:
        return presser_id == challenge["opponent_id"]
    if challenge["opponent_username"]:
        return (presser_username or "").lower() == challenge["opponent_username"].lower()
    return False


@register_callback("challenge_accept")
@serialize_by_challenge
async def on_challenge_accept(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    print(f"[match] challenge_accept clicked by user_id={presser['id']} for challenge_id={challenge_id}")

    challenge = await get_challenge(challenge_id)
    if not challenge or challenge["status"] != "pending":
        await safe_answer_callback(callback_query["id"], "This challenge is no longer active.", show_alert=True)
        return

    if not _presser_matches_opponent(challenge, presser["id"], presser.get("username")):
        await safe_answer_callback(callback_query["id"], "🚫 This isn't your challenge to accept!", show_alert=True)
        return

    if not await has_minimum_team(int(presser["id"])):
        await safe_answer_callback(
            callback_query["id"],
            "⚠️ You need a minimum 11 players team to join this game.",
            show_alert=True,
        )
        return

    xi_ok, _xi_reason = await get_playing_xi_status(int(presser["id"]))
    if not xi_ok:
        await safe_answer_callback(
            callback_query["id"],
            "⚠️ Your Playing XI is not perfect. Need min 3 batsman, 1 wicket-keeper, 3 all-rounder and 3 bowlers.",
            show_alert=True,
        )
        return

    cancel_timer("challenge", challenge_id)

    if challenge["opponent_id"] is None:
        await set_opponent_id(challenge_id, presser["id"])
        challenge["opponent_id"] = presser["id"]

    await update_status(challenge_id, "accepted")
    await safe_answer_callback(callback_query["id"], "✅ Challenge Accepted!")

    challenger_display = mention(challenge["challenger_id"], challenge["challenger_username"], challenge["challenger_name"])
    opponent_display = mention(presser["id"], presser.get("username"), presser.get("first_name"))

    session = await _get_session(chat_id, challenge)
    session.message_id = challenge["message_id"]
    session.meta["challenger_display"] = challenger_display
    session.meta["opponent_display"] = opponent_display
    MATCH_ENGINE.confirm_challenge(session)

    try:
        await app.edit_message_text(
            chat_id,
            challenge["message_id"],
            match_confirmed_message(challenger_display, opponent_display, challenge["format"]),
            parse_mode="Markdown",
            reply_markup=NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[match] Non-fatal edit_message_text error after accept: {exc!r}")

    print(f"[match] Challenge {challenge_id} confirmed. Waiting 2s before toss...")

    try:
        await asyncio.sleep(2)
        await _start_toss_stage(challenge_id, chat_id, challenger_display)
    except Exception as exc:
        print(f"[match] Fatal error while starting toss stage: {exc!r}")
        raise


@register_callback("toss_call")
@serialize_by_challenge
async def on_toss_call(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    call = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    print(f"[match] toss_call={call} clicked by user_id={presser['id']} for challenge_id={challenge_id}")

    challenge = await get_challenge(challenge_id)
    if not challenge or challenge["status"] != "accepted":
        await safe_answer_callback(callback_query["id"], "The toss isn't active right now.", show_alert=True)
        return

    if presser["id"] != challenge["challenger_id"]:
        await safe_answer_callback(callback_query["id"], "🚫 Only the challenger can call the toss!", show_alert=True)
        return

    cancel_timer("toss_call", challenge_id)
    await safe_answer_callback(callback_query["id"], f"You called {call}!")
    await _resolve_toss_and_advance(challenge_id, chat_id, call)


async def _resolve_toss_and_advance(challenge_id, chat_id, call):
    challenge = await get_challenge(challenge_id)
    actual, caller_won = resolve_toss(call)
    winner_id = challenge["challenger_id"] if caller_won else challenge["opponent_id"]
    await set_toss(challenge_id, winner_id, call, actual)
    print(f"[match] Toss for challenge_id={challenge_id}: call={call} actual={actual} winner_id={winner_id}")

    try:
        await app.delete_message(chat_id, challenge["message_id"])
    except Exception as e:
        print(f"[match] !! Failed to delete toss message (continuing anyway): {e!r}")

    caller_display = mention(challenge["challenger_id"], challenge["challenger_username"], challenge["challenger_name"])
    if winner_id == challenge["challenger_id"]:
        winner_display = caller_display
    else:
        winner_display = mention(winner_id, challenge["opponent_username"], challenge["opponent_name"])

    text = toss_result_message(caller_display, call, actual, winner_display)
    sent = await app.send_message(chat_id, text, parse_mode="Markdown", reply_markup=bat_bowl_keyboard(challenge_id))
    await set_message_id(challenge_id, sent["message_id"])
    print(f"[match] Toss result sent for challenge_id={challenge_id}, winner_id={winner_id}, message_id={sent['message_id']}")

    async def on_reminder(remaining):
        await app.send_message(chat_id, reminder_message(winner_display, remaining, "choose Bat or Bowl"), parse_mode="Markdown")

    async def on_timeout():
        print(f"[match] Decision timed out for challenge_id={challenge_id}, auto-choosing 'bat'.")
        current = await get_challenge(challenge_id)
        if current["status"] != "toss_done":
            return
        await _finalize_decision(challenge_id, chat_id, "bat")

    start_timer("decision", challenge_id, on_reminder, on_timeout, total_seconds=90)


@register_callback("challenge_decline")
@serialize_by_challenge
async def on_challenge_decline(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    print(f"[match] challenge_decline clicked by user_id={presser['id']} for challenge_id={challenge_id}")

    challenge = await get_challenge(challenge_id)
    if not challenge or challenge["status"] != "pending":
        await safe_answer_callback(callback_query["id"], "This challenge is no longer active.", show_alert=True)
        return

    if not _presser_matches_opponent(challenge, presser["id"], presser.get("username")):
        await safe_answer_callback(callback_query["id"], "🚫 This isn't your challenge to decline!", show_alert=True)
        return

    cancel_timer("challenge", challenge_id)
    await update_status(challenge_id, "declined")
    await safe_answer_callback(callback_query["id"], "Challenge declined.")

    challenger_display = mention(challenge["challenger_id"], challenge["challenger_username"], challenge["challenger_name"])
    opponent_display = mention(presser["id"], presser.get("username"), presser.get("first_name"))

    await app.edit_message_text(chat_id, challenge["message_id"], challenge_declined_message(challenger_display, opponent_display), parse_mode="Markdown", reply_markup=NO_KEYBOARD)


@register_callback("decision")
@serialize_by_challenge
async def on_decision(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    decision = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    print(f"[match] decision={decision} clicked by user_id={presser['id']} for challenge_id={challenge_id}")

    challenge = await get_challenge(challenge_id)
    if not challenge or challenge["status"] != "toss_done":
        await safe_answer_callback(callback_query["id"], "The decision isn't active right now.", show_alert=True)
        return

    if presser["id"] != challenge["toss_winner_id"]:
        await safe_answer_callback(callback_query["id"], "🚫 Only the toss winner can decide!", show_alert=True)
        return

    cancel_timer("decision", challenge_id)
    await safe_answer_callback(callback_query["id"], f"You chose to {decision}!")
    await _finalize_decision(challenge_id, chat_id, decision)


async def _finalize_decision(challenge_id, chat_id, decision):
    await set_decision(challenge_id, decision)
    challenge = await get_challenge(challenge_id)
    session = await _get_session(chat_id, challenge)
    MATCH_ENGINE.record_decision(session, decision)
    session.stage = "lineup"

    if challenge["toss_winner_id"] == challenge["challenger_id"]:
        winner_display = mention(challenge["challenger_id"], challenge["challenger_username"], challenge["challenger_name"])
    else:
        winner_display = mention(challenge["opponent_id"], challenge["opponent_username"], challenge["opponent_name"])

    await app.edit_message_text(chat_id, challenge["message_id"], match_starting_message(winner_display, decision, challenge["format"]), parse_mode="Markdown", reply_markup=NO_KEYBOARD)
    print(f"[match] Decision finalized for challenge_id={challenge_id}: {decision}. Lineup selection begins next.")

    await asyncio.sleep(1)
    await _start_opening_selection(chat_id, challenge, decision)


@register_callback("lineup_bat")
@serialize_by_challenge
async def on_lineup_bat(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    player_id = int(data_parts[2])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This lineup is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    batting_team_id = int(session.meta.get("batting_team_id") or 0)
    if not _is_team_member_allowed(presser["id"], batting_team_id):
        await safe_answer_callback(callback_query["id"], "🚫 Only the batting side can choose openers.", show_alert=True)
        return

    if session.meta.get("lineup_bowling_started"):
        await safe_answer_callback(callback_query["id"], "Openers are locked. The bowler is being chosen now.", show_alert=True)
        return

    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    player = find_player_by_id(batting_squad, player_id)
    if player is None:
        await safe_answer_callback(callback_query["id"], "That player is not in your squad.", show_alert=True)
        return

    striker_id = session.meta.get("selected_striker_id")
    non_striker_id = session.meta.get("selected_non_striker_id")
    action_text = None

    if striker_id is not None and int(striker_id) == player_id:
        session.meta["selected_striker_id"] = None
        action_text = f"Removed {player['name']} from striker."
    elif non_striker_id is not None and int(non_striker_id) == player_id:
        session.meta["selected_non_striker_id"] = None
        action_text = f"Removed {player['name']} from non-striker."
    elif striker_id is None:
        session.meta["selected_striker_id"] = player_id
        action_text = f"Selected {player['name']} as striker!"
    elif non_striker_id is None and int(striker_id) != player_id:
        session.meta["selected_non_striker_id"] = player_id
        action_text = f"Selected {player['name']} as non-striker!"
    else:
        await safe_answer_callback(callback_query["id"], "Openers are already locked.", show_alert=True)
        return

    selected_striker_id = session.meta.get("selected_striker_id")
    selected_non_striker_id = session.meta.get("selected_non_striker_id")
    selected_ids = {int(x) for x in [selected_striker_id, selected_non_striker_id] if x is not None}

    await safe_answer_callback(callback_query["id"], action_text or f"Selected {player['name']}!")
    await safe_edit_message_text(
        chat_id,
        int(session.meta["lineup_message_id"]),
        render_opening_selection_message(
            session.meta.get("batting_display") or "Batting Side",
            session.meta.get("bowling_display") or "Bowling Side",
            batting_squad,
            selected_striker_id=selected_striker_id,
            selected_non_striker_id=selected_non_striker_id,
        ),
        parse_mode="Markdown",
        reply_markup=player_selection_keyboard("lineup_bat", challenge_id, batting_squad, selected_ids=selected_ids),
    )

    if selected_striker_id is not None and selected_non_striker_id is not None and not session.meta.get("lineup_bowling_started"):
        session.meta["lineup_bowling_started"] = True
        await _complete_openers_and_prompt_bowler(chat_id, challenge, session)


async def _complete_openers_and_prompt_bowler(chat_id: int, challenge: dict[str, Any], session):
    batting_team_id = int(session.meta["batting_team_id"])
    bowling_team_id = int(session.meta["bowling_team_id"])
    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    bowlers = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []

    striker = find_player_by_id(batting_squad, int(session.meta["selected_striker_id"]))
    non_striker = find_player_by_id(batting_squad, int(session.meta["selected_non_striker_id"]))
    if striker is None or non_striker is None:
        await app.send_message(chat_id, "⚠️ Opening batsmen could not be resolved.")
        return

    batting_order = reorder_openers(batting_squad, int(striker["player_id"]), int(non_striker["player_id"]))
    MATCH_ENGINE.set_batting_order(session, batting_order)
    session.innings = create_innings(
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        batting_order=batting_order,
        innings_number=1,
    )
    session.current_batsman = striker
    session.current_bowler = None
    MATCH_ENGINE.set_bowling_order(session, bowling_candidates(bowlers))
    session.stage = "lineup_bowling"
    session.meta["lineup_bowling_started"] = True

    await app.send_message(
        chat_id,
        "✅ Opening batsmen locked. Now the bowling side will choose a bowler.",
        parse_mode="Markdown",
    )

    await _start_bowler_selection(chat_id, challenge, batting_team_id, bowling_team_id)


@register_callback("lineup_bowl")
@serialize_by_challenge
async def on_lineup_bowl(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    player_id = int(data_parts[2])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This bowler selection is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if session.meta.get("match_playing_started"):
        await safe_answer_callback(callback_query["id"], "Lineup is already locked.", show_alert=True)
        return

    bowling_team_id = int(session.meta.get("bowling_team_id") or 0)
    if not _is_team_member_allowed(presser["id"], bowling_team_id):
        await safe_answer_callback(callback_query["id"], "🚫 Only the bowling side can choose the bowler.", show_alert=True)
        return

    bowling_squad = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []
    player = find_player_by_id(bowling_squad, player_id)
    if player is None:
        await safe_answer_callback(callback_query["id"], "That player is not in your bowling squad.", show_alert=True)
        return

    selected_bowler_id = session.meta.get("selected_bowler_id")
    if selected_bowler_id is not None and int(selected_bowler_id) == player_id:
        session.meta["selected_bowler_id"] = None
        session.current_bowler = None
        await safe_answer_callback(callback_query["id"], f"Removed {player['name']} from bowler.")
        await safe_edit_message_text(
            chat_id,
            int(session.meta["bowler_message_id"]),
            render_bowler_selection_message(
                session.meta.get("bowling_display") or "Bowling Side",
                session.meta.get("batting_display") or "Batting Side",
                find_player_by_id(session.meta.get("batting_squad") or [], int(session.meta.get("selected_striker_id") or 0)),
                find_player_by_id(session.meta.get("batting_squad") or [], int(session.meta.get("selected_non_striker_id") or 0)),
            ),
            parse_mode="Markdown",
            reply_markup=player_selection_keyboard("lineup_bowl", challenge_id, bowling_candidates(bowling_squad), selected_ids=set()),
        )
        return

    session.meta["selected_bowler_id"] = player_id
    session.current_bowler = player
    MATCH_ENGINE.set_bowling_order(session, [player] + [p for p in bowling_candidates(bowling_squad) if int(p.get("player_id") or 0) != player_id])

    batting_team_id = int(session.meta["batting_team_id"])
    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    striker = find_player_by_id(batting_squad, int(session.meta["selected_striker_id"]))
    non_striker = find_player_by_id(batting_squad, int(session.meta["selected_non_striker_id"]))

    await safe_answer_callback(callback_query["id"], f"Selected {player['name']}!")
    await safe_edit_message_text(
        chat_id,
        int(session.meta["bowler_message_id"]),
        render_bowler_selection_message(
            session.meta.get("bowling_display") or "Bowling Side",
            session.meta.get("batting_display") or "Batting Side",
            striker,
            non_striker,
        ),
        parse_mode="Markdown",
        reply_markup=player_selection_keyboard("lineup_bowl", challenge_id, bowling_candidates(bowling_squad), selected_ids={player_id}),
    )

    session.meta["match_playing_started"] = True
    await _finalize_lineup(chat_id, challenge)


async def _start_delivery_stage(chat_id: int, challenge: dict[str, Any], session):
    batting_team_id = int(session.meta["batting_team_id"])
    bowling_team_id = int(session.meta["bowling_team_id"])
    batting_squad = session.meta.get("batting_squad") or await get_team_squad(batting_team_id) or []
    bowling_squad = session.meta.get("bowling_squad") or await get_team_squad(bowling_team_id) or []
    bowler = session.current_bowler or find_player_by_id(bowling_squad, int(session.meta.get("selected_bowler_id") or 0))
    if session.innings and session.innings.striker:
        striker = find_player_by_id(batting_squad, next((p.get("player_id") for p in batting_squad if p.get("name") == session.innings.striker.name), 0)) \
            or {"name": session.innings.striker.name}
    else:
        striker = find_player_by_id(batting_squad, int(session.meta.get("selected_striker_id") or 0))
    if session.innings and session.innings.non_striker:
        non_striker = find_player_by_id(batting_squad, next((p.get("player_id") for p in batting_squad if p.get("name") == session.innings.non_striker.name), 0)) \
            or {"name": session.innings.non_striker.name}
    else:
        non_striker = find_player_by_id(batting_squad, int(session.meta.get("selected_non_striker_id") or 0))
    bowling_display = session.meta.get("bowling_display") or "Bowling Side"
    batting_display = session.meta.get("batting_display") or "Batting Side"

    session.stage = "delivery_type"
    MATCH_ENGINE.reset_ball_plan(session)

    message = [
        _live_scorecard_text(session),
        "",
        "*Bowler, choose your delivery type.*",
        "*Then set line and length.*",
    ]

    sent = await app.send_message(
        chat_id,
        "\n".join(message),
        parse_mode="Markdown",
        reply_markup=delivery_type_keyboard(challenge["challenge_id"]),
    )
    session.meta["delivery_message_id"] = sent["message_id"]
    print(f"[match] Delivery stage started for challenge_id={challenge['challenge_id']}, message_id={sent['message_id']}")


async def _edit_delivery_stage(chat_id: int, challenge_id: int, session, text: str, reply_markup: dict | None):
    message_id = session.meta.get("delivery_message_id")
    if message_id is None:
        sent = await app.send_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup)
        session.meta["delivery_message_id"] = sent["message_id"]
        return
    await app.edit_message_text(chat_id, int(message_id), text, parse_mode="Markdown", reply_markup=reply_markup)


def _delivery_state_text(session, label: str) -> str:
    bowler = session.current_bowler.get("name") if session.current_bowler else "Bowler"
    parts = [
        _live_scorecard_text(session),
        "",
        f"*🎯 {label}*",
    ]
    if session.selected_delivery:
        parts.append(f"Delivery: {session.selected_delivery}")
    if session.selected_line:
        parts.append(f"Line: {session.selected_line}")
    if session.selected_length:
        parts.append(f"Length: {session.selected_length}")
    if session.selected_foot:
        parts.append(f"Foot: {session.selected_foot}")
    if session.selected_stroke_type:
        parts.append(f"Stroke: {session.selected_stroke_type}")
    return "\n".join(parts)


@register_callback("delivery_type")
@serialize_by_challenge
async def on_delivery_type(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    delivery_type = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This delivery screen is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    bowling_team_id = int(session.meta.get("bowling_team_id") or 0)
    if not _is_team_member_allowed(presser["id"], bowling_team_id):
        await safe_answer_callback(callback_query["id"], "🚫 Only the bowling side can choose the delivery.", show_alert=True)
        return

    if session.selected_delivery == delivery_type:
        MATCH_ENGINE.set_delivery_type(session, None)
        await safe_answer_callback(callback_query["id"], f"Deselected {delivery_type}.")
    else:
        MATCH_ENGINE.set_delivery_type(session, delivery_type)
        await safe_answer_callback(callback_query["id"], f"Selected {delivery_type}.")

    text = _delivery_state_text(session, "DELIVERY TYPE")
    await app.edit_message_text(
        chat_id,
        int(session.meta["delivery_message_id"]),
        text + "\n\n*Now choose the line.*",
        parse_mode="Markdown",
        reply_markup=line_keyboard(challenge_id),
    )
    session.stage = "delivery_line"


@register_callback("delivery_line")
@serialize_by_challenge
async def on_delivery_line(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    line = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This line screen is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if not _is_team_member_allowed(presser["id"], int(session.meta.get("bowling_team_id") or 0)):
        await safe_answer_callback(callback_query["id"], "🚫 Only the bowling side can choose the line.", show_alert=True)
        return

    if session.selected_line == line:
        MATCH_ENGINE.set_line(session, None)
        await safe_answer_callback(callback_query["id"], f"Deselected {line}.")
    else:
        MATCH_ENGINE.set_line(session, line)
        await safe_answer_callback(callback_query["id"], f"Selected {line}.")

    if session.selected_delivery in {"Yorker Ball", "Bouncer Ball"}:
        await app.edit_message_text(
            chat_id,
            int(session.meta["delivery_message_id"]),
            _delivery_state_text(session, "LINE LOCKED") + "\n\n*Next: batting side chooses foot movement.*",
            parse_mode="Markdown",
            reply_markup=foot_movement_keyboard(challenge_id),
        )
        session.stage = "foot_movement"
    else:
        await app.edit_message_text(
            chat_id,
            int(session.meta["delivery_message_id"]),
            _delivery_state_text(session, "LINE LOCKED") + "\n\n*Now choose the length.*",
            parse_mode="Markdown",
            reply_markup=length_keyboard(challenge_id),
        )
        session.stage = "delivery_length"


@register_callback("delivery_length")
@serialize_by_challenge
async def on_delivery_length(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    length = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This length screen is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if not _is_team_member_allowed(presser["id"], int(session.meta.get("bowling_team_id") or 0)):
        await safe_answer_callback(callback_query["id"], "🚫 Only the bowling side can choose the length.", show_alert=True)
        return

    if session.selected_length == length:
        MATCH_ENGINE.set_length(session, None)
        await safe_answer_callback(callback_query["id"], f"Deselected {length}.")
    else:
        MATCH_ENGINE.set_length(session, length)
        await safe_answer_callback(callback_query["id"], f"Selected {length}.")

    await app.edit_message_text(
        chat_id,
        int(session.meta["delivery_message_id"]),
        _delivery_state_text(session, "LENGTH LOCKED") + "\n\n*Batting side, choose foot movement.*",
        parse_mode="Markdown",
        reply_markup=foot_movement_keyboard(challenge_id),
    )
    session.stage = "foot_movement"


@register_callback("foot")
@serialize_by_challenge
async def on_foot_movement(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    foot = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This foot stage is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if not _is_team_member_allowed(presser["id"], int(session.meta.get("batting_team_id") or 0)):
        await safe_answer_callback(callback_query["id"], "🚫 Only the batting side can choose the foot movement.", show_alert=True)
        return

    MATCH_ENGINE.set_foot_movement(session, foot)
    await safe_answer_callback(callback_query["id"], f"Selected {foot}.")
    await app.edit_message_text(
        chat_id,
        int(session.meta["delivery_message_id"]),
        _delivery_state_text(session, "FOOT MOVEMENT LOCKED") + "\n\n*Now choose the stroke type.*",
        parse_mode="Markdown",
        reply_markup=stroke_type_keyboard(challenge_id),
    )
    session.stage = "stroke_type"


@register_callback("stroke_type")
@serialize_by_challenge
async def on_stroke_type(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    stroke_type = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This stroke stage is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if not _is_team_member_allowed(presser["id"], int(session.meta.get("batting_team_id") or 0)):
        await safe_answer_callback(callback_query["id"], "🚫 Only the batting side can choose the stroke type.", show_alert=True)
        return

    MATCH_ENGINE.set_stroke_type(session, stroke_type)
    await safe_answer_callback(callback_query["id"], f"Selected {stroke_type}.")
    bowler_type = "Pace" if session.selected_delivery not in {None, "Slow Spin"} else "Spin"
    shots = shot_keyboard(challenge_id, session.selected_foot or "Front Foot", stroke_type, bowler_type)
    await app.edit_message_text(
        chat_id,
        int(session.meta["delivery_message_id"]),
        _delivery_state_text(session, "STROKE TYPE LOCKED") + "\n\n*Now choose the specific shot.*",
        parse_mode="Markdown",
        reply_markup=shots,
    )
    session.stage = "shot_choice"


@register_callback("shot")
@serialize_by_challenge
async def on_shot_choice(callback_query):
    data_parts = callback_query["data"].split(":", 2)
    challenge_id = int(data_parts[1])
    shot = data_parts[2]
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This shot stage is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    if not _is_team_member_allowed(presser["id"], int(session.meta.get("batting_team_id") or 0)):
        await safe_answer_callback(callback_query["id"], "🚫 Only the batting side can choose the shot.", show_alert=True)
        return

    MATCH_ENGINE.set_shot(session, shot)
    await safe_answer_callback(callback_query["id"], f"Selected {shot}.")

    batter_name_before = MATCH_ENGINE.current_batsman_name(session)
    bowler_name_before = MATCH_ENGINE.current_bowler_name(session)

    result = MATCH_ENGINE.resolve_ball(session)
    outcome = result.get("outcome") or ""
    runs = int(result.get("runs", 0) or 0)
    wicket = bool(result.get("wicket"))
    legal_delivery = outcome not in {"wide", "no_ball"}

    _update_ball_stats(session, batter_name_before, bowler_name_before, outcome, runs, wicket, legal_delivery)

    batting_squad = session.meta.get("batting_squad") or []
    if session.innings and session.innings.striker:
        new_striker_id = next((p.get("player_id") for p in batting_squad if p.get("name") == session.innings.striker.name), None)
        if new_striker_id is not None:
            new_striker_player = find_player_by_id(batting_squad, int(new_striker_id))
            if new_striker_player:
                session.current_batsman = new_striker_player

    over_ended = bool(legal_delivery and session.innings and session.innings.score.legal_balls > 0 and session.innings.score.balls == 0)
    innings_done = bool(session.innings and session.innings.completed)
    match_done = MATCH_ENGINE.is_match_complete(session)

    try:
        commentary = result.get("commentary") or "Ball resolved."
        await app.edit_message_text(
            chat_id,
            int(session.meta["delivery_message_id"]),
            _live_scorecard_text(session) + f"\n\n_{commentary}_",
            parse_mode="Markdown",
            reply_markup=NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[match] Failed to update delivery message after shot: {exc!r}")

    MATCH_ENGINE.reset_ball_plan(session)

    if match_done or innings_done:
        if session.innings.innings_number == 1:
            await asyncio.sleep(1)
            await _start_second_innings(chat_id, challenge, session)
        else:
            await asyncio.sleep(1)
            await _finish_match(chat_id, challenge, session)
        return

    if over_ended:
        over_text = _over_summary_text(session)
        session.meta["current_over_events"] = []
        sent = await app.send_message(chat_id, over_text, parse_mode="Markdown")
        await asyncio.sleep(3)
        try:
            await app.delete_message(chat_id, sent["message_id"])
        except Exception as exc:
            print(f"[match] Failed to delete over summary message: {exc!r}")

        bowling_squad = session.meta.get("bowling_squad") or []
        last_bowler_id = session.meta.get("selected_bowler_id")
        candidates = [p for p in bowling_candidates(bowling_squad) if str(p.get("player_id")) != str(last_bowler_id)]
        if not candidates:
            candidates = bowling_candidates(bowling_squad)

        bowling_display = session.meta.get("bowling_display") or "Bowling Side"
        batting_display = session.meta.get("batting_display") or "Batting Side"
        striker_dict = {"name": session.innings.striker.name} if session.innings and session.innings.striker else None
        non_striker_dict = {"name": session.innings.non_striker.name} if session.innings and session.innings.non_striker else None
        text = f"{bowling_display}\n" + render_bowler_selection_message(bowling_display, batting_display, striker_dict, non_striker_dict)
        sent2 = await app.send_message(
            chat_id,
            text,
            parse_mode="Markdown",
            reply_markup=player_selection_keyboard("next_bowler", challenge["challenge_id"], candidates),
        )
        session.meta["delivery_message_id"] = sent2["message_id"]
        session.meta["selected_bowler_id"] = None
        session.stage = "select_next_bowler"
        print(f"[match] Over complete for challenge_id={challenge_id}. Prompting new bowler.")
        return

    session.stage = "delivery_type"
    await asyncio.sleep(1)
    await _start_delivery_stage(chat_id, challenge, session)


@register_callback("next_bowler")
@serialize_by_challenge
async def on_next_bowler_choice(callback_query):
    data_parts = callback_query["data"].split(":")
    challenge_id = int(data_parts[1])
    player_id = int(data_parts[2])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]

    challenge = await get_challenge(challenge_id)
    if not challenge:
        await safe_answer_callback(callback_query["id"], "This bowler selection is no longer active.", show_alert=True)
        return

    session = await _get_session(chat_id, challenge)
    bowling_team_id = int(session.meta.get("bowling_team_id") or 0)
    if not _is_team_member_allowed(presser["id"], bowling_team_id):
        await safe_answer_callback(callback_query["id"], "🚫 Only the bowling side can choose the bowler.", show_alert=True)
        return

    bowling_squad = session.meta.get("bowling_squad") or []
    player = find_player_by_id(bowling_squad, player_id)
    if player is None:
        await safe_answer_callback(callback_query["id"], "That bowler isn't available.", show_alert=True)
        return

    session.current_bowler = player
    session.meta["selected_bowler_id"] = player_id
    await safe_answer_callback(callback_query["id"], f"Selected {player['name']}!")

    await _start_delivery_stage(chat_id, challenge, session)
