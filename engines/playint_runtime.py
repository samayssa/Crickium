"""Runtime state and simulation helpers for the /playint mode."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.approaches import BallContext, BallOutcome
from engines.innings_engine import BatterSlot, InningsState, create_innings, register_ball
from engines.lineup_engine import bowling_candidates
from engines.play_engine import playing_xi
from engines.strategy_engine import resolve as resolve_strategy


@dataclass(slots=True)
class OverEvent:
    symbol: str
    outcome: str
    runs: int
    wicket: bool
    legal: bool
    description: str


@dataclass(slots=True)
class PlaySession:
    match_id: int
    chat_id: int
    match: dict[str, Any]
    pitch: str
    stadium: str
    weather: str
    batting_team_id: int
    bowling_team_id: int
    batting_team_display: str
    bowling_team_display: str
    batting_squad: list[dict[str, Any]]
    bowling_squad: list[dict[str, Any]]
    batting_xi: list[dict[str, Any]]
    bowling_pool: list[dict[str, Any]]
    innings: InningsState
    current_bowler: dict[str, Any] | None = None
    current_strategy: str | None = None
    current_tactic: str | None = None
    live_message_id: int | None = None
    ready_message_id: int | None = None
    short_message_id: int | None = None
    selected_bowler_id: int | None = None
    stage: str = "choose_bowler"
    this_over: list[str] = field(default_factory=list)
    bowler_stats: dict[int, dict[str, int]] = field(default_factory=dict)
    partnership_runs: int = 0
    partnership_balls: int = 0
    innings_history: list[dict[str, Any]] = field(default_factory=list)


_PLAYINT_SESSIONS: dict[int, PlaySession] = {}


def create_playint_session(
    *,
    match_id: int,
    chat_id: int,
    match: dict[str, Any],
    pitch: str,
    stadium: str,
    weather: str,
    batting_team_id: int,
    bowling_team_id: int,
    batting_team_display: str,
    bowling_team_display: str,
    batting_squad: list[dict[str, Any]],
    bowling_squad: list[dict[str, Any]],
) -> PlaySession:
    batting_xi = playing_xi(batting_squad)
    bowl_pool = bowling_candidates(bowling_squad)
    innings = create_innings(
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        batting_order=batting_xi,
    )
    session = PlaySession(
        match_id=match_id,
        chat_id=chat_id,
        match=match,
        pitch=pitch,
        stadium=stadium,
        weather=weather,
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        batting_team_display=batting_team_display,
        bowling_team_display=bowling_team_display,
        batting_squad=batting_squad,
        bowling_squad=bowling_squad,
        batting_xi=batting_xi,
        bowling_pool=bowl_pool,
        innings=innings,
    )
    _PLAYINT_SESSIONS[match_id] = session
    return session


def get_playint_session(match_id: int) -> PlaySession | None:
    return _PLAYINT_SESSIONS.get(int(match_id))


def get_playint_session_in_chat(chat_id: int) -> PlaySession | None:
    """Return the live in-memory session currently occupying a group."""
    target = int(chat_id)
    for session in _PLAYINT_SESSIONS.values():
        if int(session.chat_id) == target:
            return session
    return None


def clear_playint_session(match_id: int) -> None:
    _PLAYINT_SESSIONS.pop(int(match_id), None)


# Standard T20 rule: no bowler may bowl more than a fifth of the innings.
MAX_OVERS_PER_BOWLER = 4


def bowler_overs_bowled(session: PlaySession, player_id: int) -> int:
    data = session.bowler_stats.get(int(player_id), {})
    return int(data.get("balls") or 0) // 6


def bowler_overs_left(session: PlaySession, player_id: int) -> int:
    return max(0, MAX_OVERS_PER_BOWLER - bowler_overs_bowled(session, player_id))


def bowler_candidates_for_next_over(session: PlaySession) -> list[dict[str, Any]]:
    candidates = bowling_candidates(session.bowling_squad)

    # A bowler who has already bowled their full quota this innings is
    # gone for good - never offered again, regardless of who bowled last.
    eligible = [
        p for p in candidates
        if bowler_overs_bowled(session, int(p.get("player_id") or 0)) < MAX_OVERS_PER_BOWLER
    ]

    # No bowler may bowl two overs in a row - exclude whoever bowled the
    # over that just finished, as long as someone else is still eligible.
    if session.selected_bowler_id is not None and len(eligible) > 1:
        without_last = [
            p for p in eligible
            if int(p.get("player_id") or 0) != int(session.selected_bowler_id)
        ]
        if without_last:
            eligible = without_last

    for player in eligible:
        player["_overs_left"] = bowler_overs_left(session, int(player.get("player_id") or 0))

    return eligible


def render_this_over(tokens: list[str]) -> str:
    if not tokens:
        return "-"
    return " • ".join(tokens)


def _format_batsman_line(slot: BatterSlot, striker: bool) -> str:
    prefix = "◉" if striker else " "
    name = (slot.name or "Player")[:22]
    runs = int(slot.runs or 0)
    balls = int(slot.balls or 0)
    return f"{prefix} {name:<22} {runs} ({balls})"


def _format_bowler_figures(session: PlaySession) -> str:
    if session.current_bowler is None:
        return "0W • 0R • 0.0 Ov"
    pid = int(session.current_bowler.get("player_id") or 0)
    data = session.bowler_stats.get(pid, {"balls": 0, "runs": 0, "wickets": 0})
    balls = int(data.get("balls") or 0)
    overs = f"{balls // 6}.{balls % 6}"
    return f"{int(data.get('wickets') or 0)}W • {int(data.get('runs') or 0)}R • {overs} Ov"


def _run_rate(runs: int, legal_balls: int) -> float:
    if legal_balls <= 0:
        return 0.0
    return (runs / legal_balls) * 6.0


def _run_rate_line(session: PlaySession) -> str:
    score = session.innings.score
    crr = _run_rate(score.runs, score.legal_balls)
    target = session.innings.target
    if target is None:
        return f"📈 CRR: {crr:.2f}"

    balls_remaining = max(0, 120 - score.legal_balls)
    runs_needed = max(0, target - score.runs)
    rrr = _run_rate(runs_needed, balls_remaining) if balls_remaining > 0 else 0.0
    return (
        f"📈 CRR: {crr:.2f} • 🎯 RRR: {rrr:.2f}\n"
        f"🏹 Need {runs_needed} runs in {balls_remaining} balls"
    )


def _profile_team_line(session: PlaySession, batting: bool) -> str:
    from utils.mentions import mention_name_only_html
    match = session.match
    if batting:
        uid = int(session.batting_team_id)
        if uid == int(match.get('challenger_id') or 0):
            mention = mention_name_only_html(match.get('challenger_id'), match.get('challenger_name'))
        else:
            mention = mention_name_only_html(match.get('opponent_id'), match.get('opponent_name'))
        return f"{mention} • {session.batting_team_display}"
    uid = int(session.bowling_team_id)
    if uid == int(match.get('challenger_id') or 0):
        mention = mention_name_only_html(match.get('challenger_id'), match.get('challenger_name'))
    else:
        mention = mention_name_only_html(match.get('opponent_id'), match.get('opponent_name'))
    return f"{mention} • {session.bowling_team_display}"


def render_live_scorecard(session: PlaySession, *, bowler_prompt: bool = False) -> str:
    striker = session.innings.striker or BatterSlot(name="Player")
    non = session.innings.non_striker or BatterSlot(name="Player")
    if bowler_prompt or session.current_bowler is None:
        bowler_line = "🥎 Choose Your Bowler"
    elif session.stage == "choose_tactic":
        bowler_line = f"🥎 {session.current_bowler.get('name', 'Bowler')}\n🎯 Choose Your Bowling Tactic"
    else:
        bowler_line = f"🥎 {session.current_bowler.get('name', 'Bowler')}"
    figures = "" if bowler_prompt or session.current_bowler is None or session.stage == "choose_tactic" else _format_bowler_figures(session)
    score = session.innings.score
    over_text = f"{score.overs}.{score.balls}"
    lines = [
        "<b>╭━━━〔 🏏 LIVE SCORE 〕━━━╮</b>",
        "",
        f"🏏 {_profile_team_line(session, True)}",
        "",
        f"📊 Score ➤ {score.runs}/{score.wickets} ({over_text} Ov)",
        _run_rate_line(session),
        "",
        _format_batsman_line(striker, True),
        _format_batsman_line(non, False),
        "",
        "🤝 Partnership",
        f"{session.partnership_runs} runs off {session.partnership_balls} balls",
        "",
        f"🎯 {_profile_team_line(session, False)}",
        "",
        bowler_line,
    ]
    if figures:
        lines.append(figures)
    lines.extend(["", f"This over: [ {render_this_over(session.this_over)} ]", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"])
    return "\n".join(lines)

def assign_bowler(session: PlaySession, player: dict[str, Any]) -> bool:
    pid = int(player.get("player_id") or 0)
    if bowler_overs_bowled(session, pid) >= MAX_OVERS_PER_BOWLER:
        return False
    session.current_bowler = dict(player)
    session.selected_bowler_id = pid
    slot = session.bowler_stats.setdefault(pid, {"balls": 0, "runs": 0, "wickets": 0, "name": player.get("name", "Bowler")})
    slot.setdefault("name", player.get("name", "Bowler"))
    session.current_tactic = None
    session.stage = "choose_tactic"
    return True


def assign_tactic(session: PlaySession, tactic: str) -> None:
    session.current_tactic = str(tactic).strip().lower()
    session.stage = "choose_strategy"


def start_new_partnership(session: PlaySession) -> None:
    session.partnership_runs = 0
    session.partnership_balls = 0


def _ball_context(session: PlaySession, strategy: str) -> BallContext:
    striker = session.innings.striker or BatterSlot(name="Player")
    bowler = session.current_bowler or {}
    over_number = int(session.innings.score.overs) + 1
    return BallContext(
        strategy=strategy,
        pitch=session.pitch,
        over_number=over_number,
        ball_number=int(session.innings.score.balls) + 1,
        batter_level=int(striker.bat_level or 0),
        bowler_level=int(bowler.get("bowl_level") or 0),
        batsman_balls_faced=int(striker.balls or 0),
        batsman_runs=int(striker.runs or 0),
        total_runs=int(session.innings.score.runs or 0),
        wickets=int(session.innings.score.wickets or 0),
        bowler_role=bowler.get("role"),
        bowler_hand=bowler.get("bowling_hand"),
        batter_name=striker.name,
        bowler_name=bowler.get("name"),
        bowler_tactic=session.current_tactic or "swinging",
        confidence=float(striker.confidence or 0.0),
        wickets_this_over=sum(1 for token in session.this_over if str(token).upper() == "W"),
    )


def _update_bowler_stats(session: PlaySession, outcome: BallOutcome) -> None:
    if session.current_bowler is None:
        return
    pid = int(session.current_bowler.get("player_id") or 0)
    bs = session.bowler_stats.setdefault(pid, {"balls": 0, "runs": 0, "wickets": 0})
    if outcome.legal:
        bs["balls"] += 1
    bs["runs"] += int(outcome.bowler_runs or 0)
    if outcome.wicket:
        bs["wickets"] += 1


def _confidence_delta(outcome_name: str, current_confidence: float, streak_before: int) -> float:
    """Tiered dot penalty (softer the more set the batter already is),
    flat increments for genuine running-between-wickets runs, and a
    +2% bonus on any boundary that continues an active streak (2nd
    consecutive boundary ball onward). Extras never move confidence."""
    if outcome_name == "dot":
        if current_confidence < 25.0:
            return -3.0
        if current_confidence < 40.0:
            return -2.0
        return -1.0
    if outcome_name == "single":
        return 1.0
    if outcome_name == "double":
        return 2.0
    if outcome_name == "triple":
        return 3.0
    if outcome_name == "four":
        return 5.0 + (2.0 if streak_before >= 1 else 0.0)
    if outcome_name == "six":
        return 6.0 + (2.0 if streak_before >= 1 else 0.0)
    return 0.0


def simulate_ball(session: PlaySession, strategy: str) -> OverEvent:
    context = _ball_context(session, strategy)
    outcome = resolve_strategy(strategy, context)
    striker_before = session.innings.striker
    if striker_before is not None:
        streak_before = int(striker_before.boundary_streak or 0)
        delta = _confidence_delta(outcome.outcome, float(striker_before.confidence or 0.0), streak_before)
        striker_before.confidence = max(0.0, min(100.0, float(striker_before.confidence or 0.0) + delta))

        if outcome.outcome in ("four", "six"):
            striker_before.boundary_streak = streak_before + 1
        elif outcome.outcome in ("dot", "single", "double", "triple"):
            striker_before.boundary_streak = 0
        # wide/no_ball/bye/leg_bye leave the streak untouched - no legal
        # batted shot happened to either extend or break it.
    register_ball(
        session.innings,
        outcome=outcome.outcome,
        runs=int(outcome.runs or 0),
        wicket=bool(outcome.wicket),
        batter_name=striker_before.name if striker_before else None,
        extra_type=outcome.extra_type,
        legal_delivery=bool(outcome.legal),
    )
    if outcome.legal:
        session.partnership_balls += 1
    session.partnership_runs += int(outcome.runs or 0)
    _update_bowler_stats(session, outcome)
    session.this_over.append(outcome.symbol)
    if outcome.wicket:
        # A new pair is now at the crease - their partnership starts
        # fresh at 0/0, not carried over from the whole innings.
        session.partnership_runs = 0
        session.partnership_balls = 0
    if len(session.this_over) > 12:
        session.this_over = session.this_over[-12:]
    return OverEvent(
        symbol=outcome.symbol,
        outcome=outcome.outcome,
        runs=int(outcome.runs or 0),
        wicket=bool(outcome.wicket),
        legal=bool(outcome.legal),
        description=outcome.symbol,
    )


def innings_completed(session: PlaySession) -> bool:
    return bool(session.innings.completed)


def over_complete_text(session: PlaySession) -> str:
    score = session.innings.score
    striker = session.innings.striker or BatterSlot(name="Player")
    non = session.innings.non_striker or BatterSlot(name="Player")
    bowler_name = session.current_bowler.get("name", "Bowler") if session.current_bowler else "Bowler"
    bowler_figures = session.bowler_stats.get(int(session.current_bowler.get("player_id") or 0), {}) if session.current_bowler else {}
    balls = int(bowler_figures.get("balls") or 0)
    overs = f"{balls // 6}.{balls % 6}"
    wickets = int(bowler_figures.get("wickets") or 0)
    runs = int(bowler_figures.get("runs") or 0)
    over_no = int(score.overs)
    over_label = "OVER" if over_no == 1 else "OVERS"
    over_suffix = "1st" if over_no == 1 else ("2nd" if over_no == 2 else ("3rd" if over_no == 3 else f"{over_no}th"))
    return (
        f"<b>╭━━━〔 {over_no} {over_label} DONE 〕━━━╮</b>\n\n"
        f"{over_suffix} over: [ {render_this_over(session.this_over)} ]\n\n"
        f"🏏 {striker.name[:22]:<22} {int(striker.runs or 0)} ({int(striker.balls or 0)})\n"
        f"🏏 {non.name[:22]:<22} {int(non.runs or 0)} ({int(non.balls or 0)})\n\n"
        f"🤝 Partnership\n"
        f"{session.partnership_runs} off {session.partnership_balls} balls\n\n"
        f"🥎 {bowler_name[:22]}\n"
        f"{wickets} wickets, {runs} runs in {overs} overs\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def next_bowler_card(session: PlaySession) -> list[dict[str, Any]]:
    return bowler_candidates_for_next_over(session)


def snapshot_innings(session: PlaySession) -> dict[str, Any]:
    """Captures everything needed to report on this innings later,
    before the session gets reset/flipped for the next innings."""
    score = session.innings.score
    batters = [
        {"player_id": b.player_id, "name": b.name, "runs": int(b.runs or 0), "balls": int(b.balls or 0), "dismissed": bool(b.dismissed)}
        for b in session.innings.batting_order
    ]
    bowlers = [
        {"player_id": int(pid), "name": data.get("name", "Bowler"), "balls": int(data.get("balls") or 0),
         "runs": int(data.get("runs") or 0), "wickets": int(data.get("wickets") or 0)}
        for pid, data in session.bowler_stats.items()
    ]
    return {
        "innings_number": session.innings.innings_number,
        "batting_team_id": session.batting_team_id,
        "bowling_team_id": session.bowling_team_id,
        "batting_team_display": session.batting_team_display,
        "bowling_team_display": session.bowling_team_display,
        "runs": int(score.runs or 0),
        "wickets": int(score.wickets or 0),
        "legal_balls": int(score.legal_balls or 0),
        "over_text": score.over_text,
        "batters": batters,
        "bowlers": bowlers,
    }


def top_batters(innings_snapshot: dict[str, Any], count: int = 2) -> list[dict[str, Any]]:
    return sorted(innings_snapshot["batters"], key=lambda b: b["runs"], reverse=True)[:count]


def top_bowlers(innings_snapshot: dict[str, Any], count: int = 2) -> list[dict[str, Any]]:
    return sorted(
        innings_snapshot["bowlers"],
        key=lambda b: (b["wickets"], -b["runs"]),
        reverse=True,
    )[:count]


def start_second_innings(session: PlaySession, target: int) -> None:
    """Flips batting/bowling sides and starts a brand new innings for
    the side that bowled first - built fresh (not next_innings(), which
    doesn't replace the batting order) since the new batting side has
    an entirely different XI."""
    new_batting_team_id = session.bowling_team_id
    new_bowling_team_id = session.batting_team_id
    new_batting_display = session.bowling_team_display
    new_bowling_display = session.batting_team_display
    new_batting_squad = session.bowling_squad
    new_bowling_squad = session.batting_squad

    batting_xi = playing_xi(new_batting_squad)
    bowl_pool = bowling_candidates(new_bowling_squad)
    innings = create_innings(
        batting_team_id=new_batting_team_id,
        bowling_team_id=new_bowling_team_id,
        batting_order=batting_xi,
        target=target,
        innings_number=2,
    )

    session.batting_team_id = new_batting_team_id
    session.bowling_team_id = new_bowling_team_id
    session.batting_team_display = new_batting_display
    session.bowling_team_display = new_bowling_display
    session.batting_squad = new_batting_squad
    session.bowling_squad = new_bowling_squad
    session.batting_xi = batting_xi
    session.bowling_pool = bowl_pool
    session.innings = innings

    session.current_bowler = None
    session.current_strategy = None
    session.current_tactic = None
    session.selected_bowler_id = None
    session.stage = "choose_bowler"
    session.this_over = []
    session.bowler_stats = {}
    session.partnership_runs = 0
    session.partnership_balls = 0


def match_winner(innings_1: dict[str, Any], innings_2: dict[str, Any]) -> tuple[int | None, str]:
    """Returns (winner_team_id, margin_description). winner_team_id is
    None for a tie."""
    target = innings_1["runs"] + 1
    score_2 = innings_2["runs"]
    if score_2 >= target:
        wickets_in_hand = 10 - innings_2["wickets"]
        return innings_2["batting_team_id"], f"by {wickets_in_hand} wicket{'s' if wickets_in_hand != 1 else ''}"
    if score_2 == innings_1["runs"]:
        return None, "Match Tied"
    margin = innings_1["runs"] - score_2
    return innings_1["batting_team_id"], f"by {margin} run{'s' if margin != 1 else ''}"


def player_of_the_match(innings_1: dict[str, Any], innings_2: dict[str, Any]) -> str:
    """Simple impact heuristic: runs + wickets*25, across both
    innings' batters and bowlers combined."""
    best_name = "Player"
    best_score = -1.0
    for snap in (innings_1, innings_2):
        for b in snap["batters"]:
            impact = b["runs"]
            if impact > best_score:
                best_score = impact
                best_name = b["name"]
        for bo in snap["bowlers"]:
            impact = bo["wickets"] * 25 - bo["runs"] * 0.2
            if impact > best_score:
                best_score = impact
                best_name = bo["name"]
    return best_name
