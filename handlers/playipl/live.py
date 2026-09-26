from __future__ import annotations

print("playipl/live.py loaded")

import asyncio
from typing import Any

from app import app
from buttons.playipl_buttons import (bowler_selection_keyboard, bowler_tactic_keyboard, strategy_keyboard, schedule_bowler_keyboard, schedule_batsman_keyboard, impact_player_keyboard, impact_batting_position_keyboard, impact_batting_role_keyboard)
from database.playipl_repo import get_match, update_status, get_teams_player_ids, set_xi
from database.user_stats_repo import add_match_xp, record_match_result, record_h2h_result
from database.player_user_stats_repo import record_match_player_stats
from services.milestones import clear_milestone_state
from services.player_match_stats import record_session_player_stats
from engines.level_engine import WIN_XP, LOSS_XP, TIE_XP
from services.match_rewards import award_competitive_rewards, build_result_caption
from database.stadium_images_repo import get_stadium_image, save_stadium_image
from engines.play_engine import pitch_label
from engines.playipl_runtime import (
    assign_bowler,
    assign_tactic,
    clear_playipl_session,
    create_playipl_session,
    get_playipl_session,
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
from utils.mentions import mention_html
from utils.PremiumEmoji import ipl_team_emoji_html
from database.playipl_teams_repo import team_name, team_color, team_short, team_label, team_short_label
from utils.stadium import random_stadium
from utils.temperature import random_weather
from handlers.registry import register_callback
from services.live_runtime_controls import (
    ensure_impact_state, reset_impact_pending, full_squad, current_xi, set_current_xi,
    impact_out_candidates, impact_in_candidates, apply_impact_replacement, future_batting_candidates,
    find_player,
    confirm_batting_order, consume_next_scheduled_bowler, scheduled_bowler_candidates, scheduled_bowler_targets,
    current_over_number, ordinal, confirm_next_bowler, clear_bowler_schedule_selection, scheduled_bowler_player, move_future_batting_player_to_position, impact_batting_position_allowed,
)
from services.super_over_bridge import build_draw_result_text, start_decider
from services.live_runtime_controls import impact_player_name_html, decorate_impact_snapshot
from utils.game_inactivity import sync_after_change

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
        print(f"[playipl] Non-fatal scorecard edit failure ignored (match_id={session.match_id}): {exc!r}")


def _team_display(match: dict[str, Any], team_id: int) -> str:
    from database.playipl_teams_repo import team_label
    if int(team_id) == int(match["challenger_id"]):
        return team_label(match.get("challenger_team_code"))
    return team_label(match.get("opponent_team_code"))


def _match_ready_text(match: dict[str, Any]) -> str:
    from database.playipl_teams_repo import team_name, team_label, team_color
    a = mention_html(match.get("challenger_id"), match.get("challenger_username"), match.get("challenger_name"))
    o = mention_html(match.get("opponent_id"), match.get("opponent_username"), match.get("opponent_name"))
    winner = int(match.get("toss_winner_id") or 0)
    toss_name = a if winner == int(match["challenger_id"]) else o
    t1, t2 = team_name(match["challenger_team_code"]).upper(), team_name(match["opponent_team_code"]).upper()
    f1, f2 = __import__("utils.PremiumEmoji", fromlist=["ipl_team_emoji_html"]).ipl_team_emoji_html(match["challenger_team_code"]), __import__("utils.PremiumEmoji", fromlist=["ipl_team_emoji_html"]).ipl_team_emoji_html(match["opponent_team_code"])
    decision = "BAT" if str(match.get("decision") or "").lower() == "bat" else "BOWL"
    return ("<b>╭━━〔 🏏 IPL MATCH READY 〕━━╮</b>\n\n"
            "<b>🏆 Indian Premier League • 20 Overs</b>\n\n"
            f"{f1} <b>{t1} XI</b>\n⚔️\n{f2} <b>{t2} XI</b>\n\n"
            f"<b>{pitch_label(str(match.get('pitch') or 'green'))} Pitch</b>\n"
            f"<b>🏟️ {match.get('stadium')}</b>\n"
            f"<b>🌡️ {match.get('weather')}</b>\n\n"
            f"<b>🪙 Toss ➤ {toss_name}</b>\n"
            f"<b>🎯 Chose to {decision}</b>\n\n"
            "<b>⚡ The IPL stage is set.\n🏏 The first ball awaits...\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>")


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
            print(f"[playipl] Cached stadium photo failed to send, falling back to search: {exc!r}")

    image_url = await find_stadium_image_url(stadium_name)
    if image_url:
        try:
            sent = await app.send_photo(chat_id, photo=image_url, caption=text, parse_mode="HTML")
            file_id = (sent.get("photo") or {}).get("file_id")
            if file_id:
                await save_stadium_image(stadium_name, file_id)
            return sent
        except Exception as exc:
            print(f"[playipl] Telegram couldn't fetch the stadium photo URL, falling back to text: {exc!r}")

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

    import json
    challenger_xi = json.loads(match.get("challenger_xi") or "[]") if isinstance(match.get("challenger_xi"), str) else list(match.get("challenger_xi") or [])
    opponent_xi = json.loads(match.get("opponent_xi") or "[]") if isinstance(match.get("opponent_xi"), str) else list(match.get("opponent_xi") or [])

    async def _resolve_team_players(team_code, xi_ids):
        ids = [int(x) for x in xi_ids if str(x).lstrip("-").isdigit()]
        if not team_code or not ids:
            return []
        rows = await get_teams_player_ids(team_code, ids)
        by_id = {int(p.get("player_id")): p for p in rows}
        return [dict(by_id[pid]) for pid in ids if pid in by_id]

    challenger_squad = await _resolve_team_players(match.get("challenger_team_code"), challenger_xi)
    opponent_squad = await _resolve_team_players(match.get("opponent_team_code"), opponent_xi)
    from database.playipl_repo import get_team_players
    challenger_full = await get_team_players(match.get("challenger_team_code"))
    opponent_full = await get_team_players(match.get("opponent_team_code"))
    match["_full_rosters"] = {
        match.get("challenger_team_code"): [dict(p) for p in challenger_full],
        match.get("opponent_team_code"): [dict(p) for p in opponent_full],
    }
    match["_selected_xis"] = {
        match.get("challenger_team_code"): [int(x) for x in challenger_xi],
        match.get("opponent_team_code"): [int(x) for x in opponent_xi],
    }
    match["_full_squads"] = {
        str(challenger_id): [dict(p) for p in challenger_full],
        str(opponent_id): [dict(p) for p in opponent_full],
    }
    batting_squad = challenger_squad if batting_team_id == challenger_id else opponent_squad
    bowling_squad = challenger_squad if bowling_team_id == challenger_id else opponent_squad
    if len(batting_squad) < 11 or len(bowling_squad) < 11:
        print(f"[playipl] Could not resolve both Playing XIs for match_id={match['match_id']}: batting={len(batting_squad)} bowling={len(bowling_squad)}")
        await update_status(int(match["match_id"]), "ended")
        return

    batting_display = _team_display(match, batting_team_id)
    bowling_display = _team_display(match, bowling_team_id)

    session = create_playipl_session(
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
    if match.get("_full_squads"):
        session.full_squads = {int(uid): [dict(p) for p in players] for uid, players in match["_full_squads"].items()}
    start_new_partnership(session)

    ready = await _send_match_ready(chat_id, match["stadium"], _match_ready_text(match))
    session.ready_message_id = ready["message_id"]
    try:
        await app.pin_chat_message(chat_id, ready["message_id"], disable_notification=True)
    except Exception as exc:
        print(f"[playipl] Failed to pin MATCH READY message: {exc!r}")

    await asyncio.sleep(1)
    live = await app.send_message(
        chat_id,
        render_live_scorecard(session, bowler_prompt=True),
        parse_mode="HTML",
        reply_markup=bowler_selection_keyboard(match["match_id"], next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get("used")),
    )
    session.live_message_id = live["message_id"]


@register_callback("playipl_bowler")
async def on_playipl_bowler(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid bowler selection.", show_alert=True)
        return
    _, match_id_str, player_id_str = parts
    match_id = int(match_id_str)
    player_id = int(player_id_str)
    session = get_playipl_session(match_id)
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

    if session.current_bowler is not None or session.stage != "choose_bowler":
        await app.answer_callback_query(callback_query["id"], "⚠️ Bowler already chosen.", show_alert=True)
        return

    if not assign_bowler(session, candidate):
        await app.answer_callback_query(callback_query["id"], "That bowler has no overs left.", show_alert=True)
        return
    session.this_over = []
    await app.answer_callback_query(callback_query["id"], f"Bowler set to {candidate.get('name')}!")
    await _safe_edit_scorecard(session, reply_markup=bowler_tactic_keyboard(match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get("used")))


@register_callback("playipl_tactic")
async def on_playipl_tactic(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid tactic.", show_alert=True)
        return
    _, match_id_str, tactic = parts
    match_id = int(match_id_str)
    session = get_playipl_session(match_id)
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
    if session.current_tactic is not None or session.stage != "choose_tactic":
        await app.answer_callback_query(callback_query["id"], "⚠️ Bowling tactic already chosen.", show_alert=True)
        return

    assign_tactic(session, tactic)
    await app.answer_callback_query(callback_query["id"], f"{tactic.replace('_', ' ').upper()} tactic set!")
    await _safe_edit_scorecard(session, reply_markup=strategy_keyboard(match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get("used")))


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




def _impact_state(session: Any) -> dict:
    state = session.impact_state
    for uid in (session.match.get('challenger_id'), session.match.get('opponent_id')):
        if uid:
            ensure_impact_state(session, int(uid))
    return state


def _impact_team_code(session: Any, team_id: int) -> str | None:
    if int(team_id) == int(session.match.get('challenger_id') or 0):
        return session.match.get('challenger_team_code')
    if int(team_id) == int(session.match.get('opponent_id') or 0):
        return session.match.get('opponent_team_code')
    return None


def _team_owner(session: Any, code: str) -> tuple[int, bool]:
    # Impact callbacks carry the Telegram user ID. Team-code callbacks can
    # still use IPL codes. Post-innings Impact screens are separate messages.
    raw = str(code)
    if raw.isdigit():
        uid = int(raw)
        if uid == int(session.match.get('challenger_id') or 0):
            return uid, True
        if uid == int(session.match.get('opponent_id') or 0):
            return uid, False
    if raw == str(session.match.get('challenger_team_code') or ''):
        return int(session.match['challenger_id']), True
    if raw == str(session.match.get('opponent_team_code') or ''):
        return int(session.match['opponent_id']), False
    return 0, False


def _impact_message_id(session: Any, owner: int) -> int | None:
    """Return the message that belongs to this owner's Impact flow."""
    st = ensure_impact_state(session, int(owner))
    if st.get('context') == 'innings_break':
        return session.impact_message_ids.get(int(owner))
    return session.live_message_id


def _impact_player_display(session: Any, team_id: int, player_id: int | None, impact_type: str) -> str:
    pid = int(player_id or 0)
    if pid <= 0:
        return 'Player'
    player = find_player(session, int(team_id), pid) or {}
    name = str(player.get('name') or 'Player')
    return impact_player_name_html(session, pid, name)


def _impact_position_label(position: int | None, player_name: str = '') -> str:
    pos = int(position or 0)
    if pos == 1:
        label = 'STRIKER'
    elif pos == 2:
        label = 'NON-STRIKER'
    elif pos > 0:
        label = f'#{pos}'
    else:
        return player_name or 'Not selected'
    return f'{label} • {player_name}' if player_name else label


def _impact_break_text(session: Any, owner: int) -> str:
    """Render a private-to-team presentation of the innings-break Impact flow."""
    owner = int(owner)
    st = ensure_impact_state(session, owner)
    code = _impact_team_code(session, owner)
    if not code:
        return '<b>⚡ IMPACT PLAYER</b>\n\nImpact Player flow unavailable.'

    team_title = f"{ipl_team_emoji_html(code)} {team_name(code)} ({team_short(code)})"
    username = mention_html(
        session.match.get('challenger_id') if owner == int(session.match.get('challenger_id') or 0) else session.match.get('opponent_id'),
        session.match.get('challenger_username') if owner == int(session.match.get('challenger_id') or 0) else session.match.get('opponent_username'),
        session.match.get('challenger_name') if owner == int(session.match.get('challenger_id') or 0) else session.match.get('opponent_name'),
    )

    out_id = int(st.get('impact_out_id') or st.get('out_id') or 0)
    in_id = int(st.get('impact_in_id') or st.get('in_id') or 0)
    out_name = _impact_player_display(session, owner, out_id, 'out') if out_id else 'Not selected'
    in_name = _impact_player_display(session, owner, in_id, 'in') if in_id else 'Not selected'

    header = (
        '<b>╭━━〔 ⚡ IMPACT PLAYER 〕━━╮</b>\n\n'
        f'👤 <b>User:</b> {username}\n'
        f'🏏 <b>Team:</b> {team_title}\n'
    )

    stage = str(st.get('stage') or 'idle')
    if stage == 'out':
        return header + '\n🎯 <b>Choose your Impact OUT player.</b>'
    if stage == 'in':
        return header + (
            f'\n❌ <b>Impact OUT:</b> {out_name}\n'
            '⚡ <b>Choose your Impact IN player.</b>'
        )
    if stage == 'batpos':
        return header + (
            f'\n❌ <b>Impact OUT:</b> {out_name}\n'
            f'⚡ <b>Impact IN:</b> {in_name}\n\n'
            f'🎯 <b>Choose {in_name} batting position.</b>'
        )

    position = st.get('confirmed_position')
    position_player = in_name
    position_line = _impact_position_label(position, position_player) if position else ''
    text = (
        header + '\n✅ <b>Impact Player confirmed!</b>\n\n'
        f'❌ <b>Impact OUT:</b> {out_name}\n'
        f'⚡ <b>Impact IN:</b> {in_name}\n'
    )
    if position_line:
        text += f'🏏 <b>Batting Position:</b> {position_line}\n'
    return text + '\n╰━━━━━━━━━━━━━━━━━━━━╯'


def _impact_text(session: Any, *, team_id: int | None = None, context: str = 'innings_break') -> str:
    _impact_state(session)
    if context == 'runtime':
        base = render_live_scorecard(session, bowler_prompt=False)
    else:
        snap = session.innings_history[0] if session.innings_history else snapshot_innings(session)
        snap = decorate_impact_snapshot(session, snap)
        code = (
            session.match.get('challenger_team_code')
            if int(snap.get('batting_team_id') or 0) == int(session.match.get('challenger_id') or 0)
            else session.match.get('opponent_team_code')
        )
        team_title = f"{ipl_team_emoji_html(code)} {snap.get('batting_team_display', session.batting_team_display)}" if code else snap.get('batting_team_display', session.batting_team_display)
        target = int(snap.get('runs') or 0) + 1
        bats = top_batters(snap)
        bowls = top_bowlers(snap)
        bat_lines = '\n'.join(f"{i+1}. {b['name']} • {b['runs']} ({b['balls']})" for i, b in enumerate(bats)) or '-'
        bowl_lines = '\n'.join(f"{i+1}. {b['name']} • {b['wickets']}W ({b['runs']}R)" for i, b in enumerate(bowls)) or '-'
        bowl_code = (
            session.match.get('challenger_team_code')
            if int(snap.get('bowling_team_id') or 0) == int(session.match.get('challenger_id') or 0)
            else session.match.get('opponent_team_code')
        )
        bowling_title = f"{ipl_team_emoji_html(bowl_code)} {snap.get('bowling_team_display', session.bowling_team_display)}" if bowl_code else snap.get('bowling_team_display', session.bowling_team_display)
        base = (
            '<b>╭━━〔 🏁 INNINGS EVENTS COMPLETE 〕━━╮</b>\n\n'
            f'🗓️ <b>{__import__("datetime").datetime.now().strftime("%d %b %Y")}</b>\n'
            f'🏏 <b>{team_title} INNINGS</b>\n'
            f'📊 <b>{snap.get("runs", 0)}/{snap.get("wickets", 0)} ({snap.get("over_text", "0.0")} Ov)</b>\n\n'
            f'<b>⭐ Top Batters</b>\n{bat_lines}\n\n'
            f'<b>🎯 Top Bowlers</b>\n{bowl_lines}\n\n'
            f'🎯 <b>Target: {target}</b> for {bowling_title}\n'
            '━━━━━━━━━━━━━━━━━━━━━━'
        )
    ids = [int(team_id)] if team_id is not None else [int(session.match['challenger_id']), int(session.match['opponent_id'])]
    chunks = [base, '', '<b>⚡ IMPACT PLAYER</b>']
    if context == 'innings_break':
        chunks.append('Before the second innings begins, each side may make one Impact Player replacement.')
    else:
        chunks.append('Select a player from your Playing XI to replace with an available substitute.')
    for uid in ids:
        st = ensure_impact_state(session, uid)
        code = _impact_team_code(session, uid)
        if not code:
            continue
        team_title = f"{ipl_team_emoji_html(code)} {team_name(code)} ({team_short(code)})"
        chunks.extend(['', f'<b>{team_title}</b>'])
        if st.get('stage') == 'in':
            out_p = next((p for p in current_xi(session, uid) if int(p.get('player_id') or 0) == int(st.get('out_id') or 0)), None)
            impact_name = impact_player_name_html(session, int(out_p.get('player_id') or 0), out_p.get("name", "Selected player")) if out_p else "Selected player"
            chunks.append(f'🔁 <b>{impact_name}</b> is OUT. Choose the Impact Player IN.')
        elif st.get('stage') == 'batpos':
            chunks.append('🧭 <b>Select the batting position for the new Impact Player.</b>')
        elif st.get('stage') == 'batrole':
            chunks.append('🏏 <b>Select whether the Impact Player enters as Striker or Non-Striker.</b>')
        elif st.get('used'):
            chunks.append('✅ <b>Impact Player already used.</b>')
            continue
        else:
            chunks.append('🎯 <b>Select the player to take OUT.</b>')
    return '\n'.join(chunks)


def _impact_markup(session: Any, *, team_id: int | None = None, context: str = 'innings_break'):
    ids = [int(team_id)] if team_id is not None else [int(session.match['challenger_id']), int(session.match['opponent_id'])]
    rows = []
    for uid in ids:
        st = ensure_impact_state(session, uid)
        if st.get('stage') == 'in':
            rows.extend(impact_player_keyboard('playipl', session.match_id, uid, impact_in_candidates(session, uid), st.get('in_id'), stage='in').inline_keyboard)
        elif st.get('stage') == 'batpos':
            rows.extend(impact_batting_position_keyboard('playipl', session.match_id, future_batting_candidates(session, uid), st.get('position')).inline_keyboard)
        elif st.get('stage') == 'batrole':
            rows.extend(impact_batting_role_keyboard('playipl', session.match_id).inline_keyboard)
        elif not st.get('used'):
            rows.extend(impact_player_keyboard('playipl', session.match_id, uid, impact_out_candidates(session, uid), st.get('out_id'), stage='out', show_cancel=(context == 'runtime')).inline_keyboard)
    return {'inline_keyboard': rows}


async def _start_impact_flow(session, innings_1_snapshot: dict[str, Any]) -> None:
    _impact_state(session)
    session.impact_message_ids.clear()
    all_users = [
        int(uid) for uid in (session.match.get('challenger_id'), session.match.get('opponent_id'))
        if uid
    ]
    unused = []
    for uid in all_users:
        st = ensure_impact_state(session, uid)
        if not st.get('used'):
            st['confirmed_position'] = None
            reset_impact_pending(session, uid, context='innings_break', return_stage=None)
            unused.append(uid)

    if not all_users:
        await _continue_after_impact_break(session)
        return

    # From the innings break onward, each participant gets a separate Impact
    # message. Only the team batting in innings two receives a batting-position
    # step after confirming its replacement.
    for uid in all_users:
        markup = None if ensure_impact_state(session, uid).get('used') else _impact_markup(
            session, team_id=uid, context='innings_break'
        )
        sent = await _safe_send(
            session.chat_id,
            _impact_break_text(session, uid),
            parse_mode='HTML',
            reply_markup=markup,
        )
        if sent and sent.get('message_id'):
            session.impact_message_ids[uid] = int(sent['message_id'])

    if not unused:
        await asyncio.sleep(3)
        await _continue_after_impact_break(session)
        return

    await sync_after_change('PLAYIPL', int(session.match_id))


async def _continue_after_impact_break(session: Any) -> None:
    for mid in list(session.impact_message_ids.values()):
        try:
            await app.delete_message(session.chat_id, int(mid))
        except Exception as exc:
            print(f'[playipl] Impact/break cleanup failed: {exc!r}')
    session.impact_message_ids.clear()
    await asyncio.sleep(1.0)
    innings_1_snapshot = session.innings_history[0] if session.innings_history else snapshot_innings(session)
    target = innings_1_snapshot['runs'] + 1
    start_second_innings(session, target)
    start_new_partnership(session)
    live = await _safe_send(
        session.chat_id,
        render_live_scorecard(session, bowler_prompt=True),
        parse_mode='HTML',
        reply_markup=bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')),
    )
    if live.get('message_id'):
        session.live_message_id = live['message_id']


async def _maybe_finish_impact_flow(session: Any) -> None:
    if session.innings.innings_number != 1:
        return
    if any(not ensure_impact_state(session, int(uid)).get('used') for uid in (session.match.get('challenger_id'), session.match.get('opponent_id'))):
        return
    await _continue_after_impact_break(session)


@register_callback('playipl_impact_runtime')
async def on_playipl_impact_runtime(callback_query):
    mid = int(callback_query['data'].split(':')[1])
    session = get_playipl_session(mid)
    if session is None:
        return
    uid = int(callback_query['from']['id'])
    if uid not in {int(session.match['challenger_id']), int(session.match['opponent_id'])}:
        await app.answer_callback_query(callback_query['id'], 'You are not part of this match.', show_alert=True)
        return
    st = ensure_impact_state(session, uid)
    if st.get('used'):
        await app.answer_callback_query(callback_query['id'], 'Your Impact Player has already been used.', show_alert=True)
        return
    reset_impact_pending(session, uid, context='runtime', return_stage=session.stage)
    await app.answer_callback_query(callback_query['id'], 'Choose your Impact Player replacement!')
    await app.edit_message_text(
        session.chat_id, session.live_message_id,
        _impact_text(session, team_id=uid, context='runtime'),
        parse_mode='HTML', reply_markup=_impact_markup(session, team_id=uid, context='runtime')
    )


@register_callback('playipl_cancel_impact')
async def on_playipl_cancel_impact(callback_query):
    _, mid_s, owner_s = callback_query['data'].split(':')
    session = get_playipl_session(int(mid_s))
    owner = int(owner_s)
    if session is None:
        return
    if int(callback_query['from']['id']) != owner:
        await app.answer_callback_query(callback_query['id'], 'These are not your Impact Player options.', show_alert=True)
        return
    st = ensure_impact_state(session, owner)
    if st.get('context') != 'runtime' or st.get('stage') != 'out':
        await app.answer_callback_query(callback_query['id'], 'This Impact Player screen can no longer be cancelled.', show_alert=True)
        return
    return_stage = st.get('return_stage') or session.stage
    st.update({'stage': 'idle', 'out_id': None, 'in_id': None, 'context': None, 'return_stage': None, 'entry_role': None, 'position': None})
    session.stage = return_stage
    await app.answer_callback_query(callback_query['id'], 'Impact Player cancelled.')
    if return_stage == 'choose_bowler':
        markup = bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = True
    elif return_stage == 'choose_tactic':
        markup = bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = False
    else:
        markup = strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used'))
        prompt = False
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=prompt), parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_out')
async def on_playipl_impact_out(callback_query):
    parts = callback_query['data'].split(':')
    if len(parts) != 4:
        await app.answer_callback_query(callback_query['id'], 'Invalid Impact Player selection.', show_alert=True)
        return
    _, mid_s, code, pid_s = parts
    mid, owner_code, pid = int(mid_s), str(code), int(pid_s)
    session = get_playipl_session(mid)
    if session is None:
        return
    owner, _ = _team_owner(session, owner_code)
    if int(callback_query['from']['id']) != owner:
        await app.answer_callback_query(callback_query['id'], 'These are not your Impact Player options.', show_alert=True)
        return
    st = ensure_impact_state(session, owner)
    if st.get('used') or st.get('stage') != 'out':
        await app.answer_callback_query(callback_query['id'], 'Complete the current Impact Player step first.', show_alert=True)
        return
    if pid not in {int(p.get('player_id') or 0) for p in impact_out_candidates(session, owner)}:
        await app.answer_callback_query(callback_query['id'], 'That player is no longer available.', show_alert=True)
        return
    # During live runtime, do not remove the active striker/non-striker. The
    # full XI is displayed as requested, but replacing the current pair would
    # leave the innings state ambiguous between deliveries.
    if st.get('context') == 'runtime':
        active_ids = {
            int(p.player_id) for p in (session.innings.striker, session.innings.non_striker)
            if p is not None and p.player_id is not None
        }
        if pid in active_ids:
            await app.answer_callback_query(callback_query['id'], 'The current striker/non-striker cannot be replaced during live play.', show_alert=True)
            return
    st['out_id'] = None if int(st.get('out_id') or -1) == pid else pid
    await app.answer_callback_query(callback_query['id'], 'Player selected.' if st['out_id'] else 'Selection removed.')
    target_mid = _impact_message_id(session, owner)
    if target_mid:
        if st.get('context') == 'innings_break':
            text = _impact_break_text(session, owner)
            markup = _impact_markup(session, team_id=owner, context='innings_break')
        else:
            text = _impact_text(session, team_id=owner, context='runtime')
            markup = _impact_markup(session, team_id=owner, context='runtime')
        await app.edit_message_text(session.chat_id, target_mid, text, parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_confirm_out')
async def on_playipl_impact_confirm_out(callback_query):
    parts = callback_query['data'].split(':')
    if len(parts) != 3:
        return
    _, mid_s, code = parts
    session = get_playipl_session(int(mid_s))
    if session is None:
        return
    owner, _ = _team_owner(session, code)
    if int(callback_query['from']['id']) != owner:
        await app.answer_callback_query(callback_query['id'], 'These are not your Impact Player options.', show_alert=True)
        return
    st = ensure_impact_state(session, owner)
    if st.get('stage') != 'out' or st.get('out_id') is None:
        await app.answer_callback_query(callback_query['id'], 'Select the player you want to replace first.', show_alert=True)
        return
    bench = impact_in_candidates(session, owner)
    if not bench:
        await app.answer_callback_query(callback_query['id'], 'No substitute player is available.', show_alert=True)
        return
    st['stage'] = 'in'
    await app.answer_callback_query(callback_query['id'], 'Choose your Impact Player!')
    target_mid = _impact_message_id(session, owner)
    if target_mid:
        if st.get('context') == 'innings_break':
            text = _impact_break_text(session, owner)
            markup = _impact_markup(session, team_id=owner, context='innings_break')
        else:
            text = _impact_text(session, team_id=owner, context='runtime')
            markup = _impact_markup(session, team_id=owner, context='runtime')
        await app.edit_message_text(session.chat_id, target_mid, text, parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_in')
async def on_playipl_impact_in(callback_query):
    parts = callback_query['data'].split(':')
    if len(parts) != 4:
        await app.answer_callback_query(callback_query['id'], 'Invalid Impact Player selection.', show_alert=True)
        return
    _, mid_s, code, pid_s = parts
    session = get_playipl_session(int(mid_s))
    if session is None:
        return
    owner, _ = _team_owner(session, code)
    if int(callback_query['from']['id']) != owner:
        await app.answer_callback_query(callback_query['id'], 'These are not your Impact Player options.', show_alert=True)
        return
    st = ensure_impact_state(session, owner)
    if st.get('stage') != 'in':
        await app.answer_callback_query(callback_query['id'], 'Confirm the OUT player first.', show_alert=True)
        return
    pid = int(pid_s)
    if pid not in {int(p.get('player_id') or 0) for p in impact_in_candidates(session, owner)}:
        await app.answer_callback_query(callback_query['id'], 'That substitute is not available.', show_alert=True)
        return
    st['in_id'] = None if int(st.get('in_id') or -1) == pid else pid
    await app.answer_callback_query(callback_query['id'], 'Impact Player selected.' if st['in_id'] else 'Selection removed.')
    target_mid = _impact_message_id(session, owner)
    if target_mid:
        if st.get('context') == 'innings_break':
            text = _impact_break_text(session, owner)
            markup = _impact_markup(session, team_id=owner, context='innings_break')
        else:
            text = _impact_text(session, team_id=owner, context='runtime')
            markup = _impact_markup(session, team_id=owner, context='runtime')
        await app.edit_message_text(session.chat_id, target_mid, text, parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_confirm_in')
async def on_playipl_impact_confirm_in(callback_query):
    parts = callback_query['data'].split(':')
    if len(parts) != 3:
        return
    _, mid_s, code = parts
    session = get_playipl_session(int(mid_s))
    if session is None:
        return
    owner, is_challenger = _team_owner(session, code)
    if int(callback_query['from']['id']) != owner:
        await app.answer_callback_query(callback_query['id'], 'These are not your Impact Player options.', show_alert=True)
        return
    st = ensure_impact_state(session, owner)
    if st.get('stage') != 'in' or st.get('out_id') is None or st.get('in_id') is None:
        await app.answer_callback_query(callback_query['id'], 'Select an Impact Player first.', show_alert=True)
        return
    replacing_current_bowler = (
        owner == int(session.bowling_team_id)
        and session.current_bowler is not None
        and int(session.current_bowler.get('player_id') or 0) == int(st['out_id'])
    )
    try:
        out_player, in_player = apply_impact_replacement(session, owner, int(st['out_id']), int(st['in_id']))
    except Exception as exc:
        await app.answer_callback_query(callback_query['id'], str(exc), show_alert=True)
        return
    new_ids = [int(p.get('player_id') or 0) for p in current_xi(session, owner)]
    session.match.setdefault('_selected_xis', {})[code] = new_ids
    await set_xi(session.match_id, owner, new_ids, is_challenger=is_challenger)
    st['used'] = True
    context = st.get('context') or 'runtime'
    await app.answer_callback_query(callback_query['id'], 'Impact Player confirmed!')

    # At an innings break, only the team that will bat in innings two gets a
    # batting-order selector. Runtime uses the same rule: batting-side now, or
    # bowling-side only during innings one because it bats next.
    if context == 'innings_break':
        # Batting-position eligibility is based on the incoming player's role.
        # This works for both participants regardless of which side bats/bowls
        # in the next innings. Bowlers confirm directly.
        if impact_batting_position_allowed(session, owner):
            st['stage'] = 'batpos'
            st['context'] = 'innings_break'
            positions = future_batting_candidates(session, owner)
            target_mid = _impact_message_id(session, owner)
            if target_mid:
                await app.edit_message_text(
                    session.chat_id, target_mid,
                    _impact_break_text(session, owner),
                    parse_mode='HTML',
                    reply_markup=impact_batting_position_keyboard('playipl', session.match_id, positions, st.get('position')),
                )
        else:
            target_mid = session.impact_message_ids.get(int(owner))
            st.update({'stage':'done', 'context':None, 'return_stage':None, 'position':None, 'entry_role':None, 'confirmed_position':None})
            if target_mid:
                await app.edit_message_text(
                    session.chat_id, target_mid, _impact_break_text(session, owner),
                    parse_mode='HTML', reply_markup={'inline_keyboard': []}
                )
            await _maybe_finish_impact_flow(session)
        return

    if impact_batting_position_allowed(session, owner):
        return_stage = 'choose_tactic' if replacing_current_bowler else (st.get('return_stage') or session.stage)
        st['return_stage'] = return_stage
        st['stage'] = 'batpos'
        st['context'] = 'runtime'
        positions = future_batting_candidates(session, owner)
        await app.edit_message_text(
            session.chat_id, session.live_message_id,
            _impact_text(session, team_id=owner, context='runtime'),
            parse_mode='HTML',
            reply_markup=impact_batting_position_keyboard('playipl', session.match_id, positions, st.get('position')),
        )
        return

    return_stage = 'choose_tactic' if replacing_current_bowler else (st.get('return_stage') or session.stage)
    st.update({'stage':'done', 'context':None, 'return_stage':None, 'position':None, 'entry_role':None})
    session.stage = return_stage
    if return_stage == 'choose_bowler':
        markup = bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')); prompt = True
    elif return_stage == 'choose_tactic':
        markup = bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')); prompt = False
    else:
        markup = strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used')); prompt = False
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=prompt), parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_batpos')
async def on_playipl_impact_batpos(callback_query):
    _, mid_s, pos_s = callback_query['data'].split(':')
    session = get_playipl_session(int(mid_s))
    if session is None:
        return
    uid = int(callback_query['from']['id'])
    st = ensure_impact_state(session, uid)
    if st.get('stage') != 'batpos':
        await app.answer_callback_query(callback_query['id'], 'Complete the Impact Player step first.', show_alert=True)
        return
    position = int(pos_s)
    positions = future_batting_candidates(session, uid)
    valid = {int(p.get('position') or 0) for p in positions}
    if position not in valid:
        await app.answer_callback_query(callback_query['id'], 'That batting position is no longer available.', show_alert=True)
        return
    st['position'] = None if int(st.get('position') or -1) == position else position
    await app.answer_callback_query(callback_query['id'], 'Position selected.' if st['position'] else 'Selection removed.')
    target_mid = _impact_message_id(session, uid)
    if target_mid:
        if st.get('context') == 'innings_break':
            text = _impact_break_text(session, uid)
        else:
            text = _impact_text(session, team_id=uid, context='runtime')
        await app.edit_message_text(session.chat_id, target_mid, text, parse_mode='HTML', reply_markup=impact_batting_position_keyboard('playipl', session.match_id, positions, st.get('position')))


@register_callback('playipl_impact_confirm_batpos')
async def on_playipl_impact_confirm_batpos(callback_query):
    mid = int(callback_query['data'].split(':')[1])
    session = get_playipl_session(mid)
    if session is None:
        return
    uid = int(callback_query['from']['id'])
    st = ensure_impact_state(session, uid)
    if st.get('stage') != 'batpos' or st.get('position') is None or st.get('in_id') is None:
        await app.answer_callback_query(callback_query['id'], 'Select the Impact Player batting position first.', show_alert=True)
        return
    try:
        if uid == int(session.batting_team_id):
            from services.live_runtime_controls import move_batting_player_to_position
            move_batting_player_to_position(session, uid, int(st['position']))
            st['stage'] = 'batrole'
            await app.answer_callback_query(callback_query['id'], 'Batting position confirmed!')
            await app.edit_message_text(session.chat_id, session.live_message_id, _impact_text(session, team_id=uid, context='runtime'), parse_mode='HTML', reply_markup=impact_batting_role_keyboard('playipl', session.match_id))
            return

        move_future_batting_player_to_position(session, uid, int(st['in_id']), int(st['position']))
    except Exception as exc:
        await app.answer_callback_query(callback_query['id'], str(exc), show_alert=True)
        return

    code = _impact_team_code(session, uid)
    if code:
        new_ids = [int(p.get('player_id') or 0) for p in current_xi(session, uid)]
        session.match.setdefault('_selected_xis', {})[code] = new_ids
        is_challenger = uid == int(session.match.get('challenger_id') or 0)
        await set_xi(session.match_id, uid, new_ids, is_challenger=is_challenger)

    context = st.get('context') or 'runtime'
    return_stage = st.get('return_stage') or session.stage
    if context == 'innings_break':
        st['confirmed_position'] = int(st.get('position') or 0)
    st.update({'stage': 'done', 'context': None, 'return_stage': None, 'position': None, 'entry_role': None})
    await app.answer_callback_query(callback_query['id'], 'Batting position confirmed!')
    if context == 'innings_break':
        target_mid = session.impact_message_ids.get(int(uid))
        if target_mid:
            await app.edit_message_text(session.chat_id, target_mid, _impact_break_text(session, uid), parse_mode='HTML', reply_markup={'inline_keyboard': []})
        await _maybe_finish_impact_flow(session)
        return
    session.stage = return_stage
    if return_stage == 'choose_bowler':
        markup = bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = True
    elif return_stage == 'choose_tactic':
        markup = bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = False
    else:
        markup = strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used'))
        prompt = False
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=prompt), parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_impact_role')
async def on_playipl_impact_role(callback_query):
    _, mid_s, role = callback_query['data'].split(':')
    session = get_playipl_session(int(mid_s))
    if session is None:
        return
    uid = int(callback_query['from']['id'])
    st = ensure_impact_state(session, uid)
    if st.get('stage') != 'batrole' or role not in {'striker', 'non_striker'}:
        await app.answer_callback_query(callback_query['id'], 'Select the batting role first.', show_alert=True)
        return
    st['entry_role'] = role
    st['stage'] = 'done'
    session.stage = st.get('return_stage') or 'choose_strategy'
    await app.answer_callback_query(callback_query['id'], f'{role.replace("_", " ").title()} selected!')
    if session.stage == 'choose_bowler':
        markup = bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = True
    elif session.stage == 'choose_tactic':
        markup = bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used'))
        prompt = False
    else:
        markup = strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used'))
        prompt = False
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=prompt), parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_set_next_bowler')
async def on_playipl_set_next_bowler(callback_query):
    mid = int(callback_query['data'].split(':')[1]); session = get_playipl_session(mid)
    if session is None: return
    uid = int(callback_query['from']['id'])
    if uid != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the bowling side can set the next bowler.', show_alert=True); return
    session.pending_next_bowler_id = None
    await app.answer_callback_query(callback_query['id'], 'Choose the next over bowler.')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=schedule_bowler_keyboard(session.match_id, scheduled_bowler_candidates(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')))


@register_callback('playipl_schedule_bowler')
async def on_playipl_schedule_bowler(callback_query):
    _, mid_s, pid_s = callback_query['data'].split(':')
    session = get_playipl_session(int(mid_s)); pid = int(pid_s)
    if session is None: return
    uid = int(callback_query['from']['id'])
    if uid != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the bowling side can set the next bowler.', show_alert=True); return
    if session.auto_bowler_enabled:
        await app.answer_callback_query(callback_query['id'], 'Turn off auto bowler before changing the schedule.', show_alert=True); return
    valid = {int(p.get('player_id') or 0) for p in scheduled_bowler_candidates(session)}
    if pid not in valid:
        await app.answer_callback_query(callback_query['id'], 'That bowler is not available.', show_alert=True); return
    session.pending_next_bowler_id = None if int(session.pending_next_bowler_id or -1) == pid else pid
    await app.answer_callback_query(callback_query['id'], 'Bowler selected.' if session.pending_next_bowler_id else 'Selection removed.')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=schedule_bowler_keyboard(session.match_id, scheduled_bowler_candidates(session), session.pending_next_bowler_id, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')))


@register_callback('playipl_confirm_next_bowler')
async def on_playipl_confirm_next_bowler(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the bowling side can confirm the next bowler.', show_alert=True); return
    had_current = session.current_bowler is not None
    try:
        pid=confirm_next_bowler(session)
    except Exception as exc:
        await app.answer_callback_query(callback_query['id'], str(exc), show_alert=True); return
    await app.answer_callback_query(callback_query['id'], 'Current-over bowler set.' if not had_current else 'Next bowler added to the plan!')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=schedule_bowler_keyboard(session.match_id, scheduled_bowler_candidates(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')))


@register_callback('playipl_start_auto_bowler')
async def on_playipl_start_auto_bowler(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the bowling side can start auto bowler.', show_alert=True); return
    if session.auto_bowler_enabled:
        await app.answer_callback_query(callback_query['id'], 'Auto bowler is already enabled.', show_alert=True); return
    if not session.auto_bowler_queue:
        await app.answer_callback_query(callback_query['id'], 'Schedule at least one next bowler first.', show_alert=True); return
    session.auto_bowler_enabled=True
    await app.answer_callback_query(callback_query['id'], 'Auto bowler enabled for the scheduled overs!')
    # Keep the current over intact. The queued bowler is consumed only when
    # the next over actually begins.
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')))


@register_callback('playipl_auto_bowler_off')
async def on_playipl_auto_bowler_off(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.bowling_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the bowling side can turn auto bowler off.', show_alert=True); return
    session.auto_bowler_enabled=False
    await app.answer_callback_query(callback_query['id'], 'Auto bowler turned off.')
    if session.stage == 'choose_tactic':
        markup=bowler_tactic_keyboard(session.match_id, session.current_bowler, False, not ensure_impact_state(session, int(session.bowling_team_id)).get("used"))
        prompt=False
    elif session.stage == 'choose_bowler':
        markup=bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, False, not ensure_impact_state(session, int(session.bowling_team_id)).get("used"))
        prompt=True
    else:
        markup=strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used'))
        prompt=False
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=prompt), parse_mode='HTML', reply_markup=markup)


@register_callback('playipl_set_next_batsman')
async def on_playipl_set_next_batsman(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    uid=int(callback_query['from']['id'])
    if uid != int(session.batting_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the batting side can set the next batsman.', show_alert=True); return
    session.pending_batsman_order.clear()
    await app.answer_callback_query(callback_query['id'], 'Select your next batting order.')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=schedule_batsman_keyboard(session.match_id, future_batting_candidates(session), session.pending_batsman_order))


@register_callback('playipl_schedule_batsman')
async def on_playipl_schedule_batsman(callback_query):
    _, mid_s, pid_s=callback_query['data'].split(':'); session=get_playipl_session(int(mid_s)); pid=int(pid_s)
    if session is None: return
    uid=int(callback_query['from']['id'])
    if uid != int(session.batting_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the batting side can set the next batsman.', show_alert=True); return
    valid={int(p.get('player_id') or 0) for p in future_batting_candidates(session)}
    if pid not in valid:
        await app.answer_callback_query(callback_query['id'], 'That batsman is not available.', show_alert=True); return
    if pid in session.pending_batsman_order:
        session.pending_batsman_order.remove(pid); msg='Selection removed.'
    else:
        session.pending_batsman_order.append(pid); msg='Batsman selected.'
    await app.answer_callback_query(callback_query['id'], msg)
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=schedule_batsman_keyboard(session.match_id, future_batting_candidates(session), session.pending_batsman_order))


@register_callback('playipl_confirm_batsman')
async def on_playipl_confirm_batsman(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.batting_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the batting side can confirm the next batsman order.', show_alert=True); return
    try:
        confirm_batting_order(session)
    except Exception as exc:
        await app.answer_callback_query(callback_query['id'], str(exc), show_alert=True); return
    await app.answer_callback_query(callback_query['id'], 'Batting order saved. Auto play enabled!')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=strategy_keyboard(session.match_id, True, not ensure_impact_state(session, int(session.batting_team_id)).get("used")))


@register_callback('playipl_cancel_batsman_schedule')
async def on_playipl_cancel_batsman_schedule(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.batting_team_id):
        return
    session.pending_batsman_order.clear()
    await app.answer_callback_query(callback_query['id'], 'Batting-order selection cancelled.')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=strategy_keyboard(session.match_id, session.auto_batsman_enabled, not ensure_impact_state(session, int(session.batting_team_id)).get('used')))


@register_callback('playipl_auto_batsman_off')
async def on_playipl_auto_batsman_off(callback_query):
    mid=int(callback_query['data'].split(':')[1]); session=get_playipl_session(mid)
    if session is None: return
    if int(callback_query['from']['id']) != int(session.batting_team_id):
        await app.answer_callback_query(callback_query['id'], 'Only the batting side can turn auto play off.', show_alert=True); return
    session.auto_batsman_enabled=False
    await app.answer_callback_query(callback_query['id'], 'Auto batsman turned off.')
    await app.edit_message_text(session.chat_id, session.live_message_id, render_live_scorecard(session, bowler_prompt=False), parse_mode='HTML', reply_markup=strategy_keyboard(session.match_id, False, not ensure_impact_state(session, int(session.batting_team_id)).get("used")))

async def _safe_send(chat_id, text, **kwargs):
    try:
        return await app.send_message(chat_id, text, **kwargs)
    except Exception as exc:
        print(f"[playipl] Non-fatal send_message failure ignored (chat_id={chat_id}): {exc!r}")
        return {}


async def _record_player_squad_stats(session, innings_1: dict, innings_2: dict) -> None:
    """Persist per-user player stats for the current PlayIPL match."""
    try:
        await record_session_player_stats(session)
    except Exception as exc:
        print(f"[playipl] Failed to persist per-player squad stats: {exc!r}")

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
        print(f"[playipl] Failed to award match XP/stats for match_id={session.match_id}: {exc!r}")

    # H2H persistence is independent from rewards/stats. A reward failure must
    # never prevent a finished match from appearing in head-to-head history.
    try:
        await record_h2h_result(
            int(session.match_id),
            int(challenger_id),
            int(opponent_id),
            None if winner_id is None else int(winner_id),
        )
    except Exception as exc:
        print(f"[playipl] Failed to record H2H history for match_id={session.match_id}: {exc!r}")


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
        print(f"[playipl] Failed to show over-complete card: {exc!r}")

    await asyncio.sleep(3)

    if session.live_message_id:
        try:
            await app.delete_message(session.chat_id, session.live_message_id)
        except Exception as exc:
            print(f"[playipl] Failed to delete over-complete card: {exc!r}")
        session.live_message_id = None

    if innings_completed(session):
        if session.innings.innings_number == 1:
            innings_1_snapshot = snapshot_innings(session)
            session.innings_history.append(innings_1_snapshot)

            await _start_impact_flow(session, innings_1_snapshot)
            return

        # Second (or later) innings just finished - the match is over.
        innings_2_snapshot = snapshot_innings(session)
        innings_1_snapshot = session.innings_history[0] if session.innings_history else innings_2_snapshot
        winner_id, margin = match_winner(innings_1_snapshot, innings_2_snapshot)
        if winner_id is None:
            draw_text = build_draw_result_text(decorate_impact_snapshot(session, innings_1_snapshot), decorate_impact_snapshot(session, innings_2_snapshot), top_batters, top_bowlers)
            await _safe_send(session.chat_id, draw_text, parse_mode="HTML")
            await asyncio.sleep(3)
            await start_decider(
                origin_engine="PLAYIPL",
                origin_match_id=int(session.match_id),
                chat_id=int(session.chat_id),
                origin_match=dict(session.match),
                innings_1=innings_1_snapshot,
                innings_2=innings_2_snapshot,
            )
            return
        winner = innings_1_snapshot["batting_team_display"] if winner_id == innings_1_snapshot["batting_team_id"] else innings_2_snapshot["batting_team_display"]
        potm_name = player_of_the_match(innings_1_snapshot, innings_2_snapshot)
        match_result_text = _match_result_text(decorate_impact_snapshot(session, innings_1_snapshot), decorate_impact_snapshot(session, innings_2_snapshot))
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
            print(f"[playipl] Summary card failed after match-result text was sent: {exc!r}")
        try:
            await update_status(session.match_id, "completed")
        except Exception as exc:
            print(f"[playipl] Failed to mark match_id={session.match_id} completed: {exc!r}")
        try:
            from services.match_notification import send_match_completion_notification
            await send_match_completion_notification(
                app, engine="PLAYIPL", pitch=session.match.get("pitch"),
                user1=(session.match.get("challenger_username"), session.match.get("challenger_name")),
                user2=(session.match.get("opponent_username"), session.match.get("opponent_name")),
                innings_1=innings_1_snapshot, innings_2=innings_2_snapshot,
                result=match_result_text,
            )
        except Exception as exc:
            print(f"[playipl] Match notification failed: {exc!r}")
        clear_milestone_state(session.match_id)
        clear_playipl_session(session.match_id)
        return

    session.last_over = list(session.this_over)
    session.last_over_commentary = list(session.over_commentary)

    session.current_bowler = None
    session.current_strategy = None
    session.current_tactic = None
    session.stage = "choose_bowler"
    session.this_over = []
    session.over_commentary = []

    # Auto-bowler consumes the next pre-planned bowler without asking again.
    if session.auto_bowler_enabled and session.auto_bowler_queue:
        player = consume_next_scheduled_bowler(session)
        if player and assign_bowler(session, player):
            session.stage = "choose_tactic"
            live = await _safe_send(
                session.chat_id,
                render_live_scorecard(session, bowler_prompt=False),
                parse_mode="HTML",
                reply_markup=bowler_tactic_keyboard(session.match_id, session.current_bowler, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')),
            )
            if live.get("message_id"):
                session.live_message_id = live["message_id"]
            return
        session.auto_bowler_enabled = False

    if session.auto_bowler_enabled and not session.auto_bowler_queue:
        session.auto_bowler_enabled = False

    live = await _safe_send(
        session.chat_id,
        render_live_scorecard(session, bowler_prompt=True),
        parse_mode="HTML",
        reply_markup=bowler_selection_keyboard(session.match_id, next_bowler_card(session), None, session.auto_bowler_enabled, not ensure_impact_state(session, int(session.bowling_team_id)).get('used')),
    )
    if live.get("message_id"):
        session.live_message_id = live["message_id"]


@register_callback("playipl_strategy")
async def on_playipl_strategy(callback_query):
    parts = callback_query["data"].split(":")
    if len(parts) < 3:
        await app.answer_callback_query(callback_query["id"], "Invalid strategy.", show_alert=True)
        return
    _, match_id_str, strategy = parts
    match_id = int(match_id_str)
    session = get_playipl_session(match_id)
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
