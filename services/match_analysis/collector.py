from __future__ import annotations

import math
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

from engines.innings_engine import BatterSlot


def record_runtime_ball(session: Any, context: Any, outcome: Any) -> None:
    """Telemetry-only hook used by /play, /playint, /playipl and /playwpl.

    Nothing here feeds the result resolver. It only records what the engine has
    already decided, which makes the HTML report reproducible after completion
    or an exit.
    """
    match = session.match
    history = match.setdefault("_analysis_balls", [])
    striker = session.innings.striker
    non_striker = session.innings.non_striker
    bowler = session.current_bowler or {}
    history.append(
        {
            "innings_number": int(session.innings.innings_number),
            "over": int(getattr(context, "over_number", 1) or 1),
            "ball": int(getattr(context, "ball_number", 1) or 1),
            "legal": bool(outcome.legal),
            "runs": int(outcome.runs or 0),
            "bowler_runs": int(getattr(outcome, "bowler_runs", outcome.runs) or 0),
            "wicket": bool(outcome.wicket),
            "outcome": str(outcome.outcome or "dot"),
            "symbol": str(outcome.symbol or _symbol_for(outcome)),
            "extra_type": getattr(outcome, "extra_type", None),
            "batter": striker.name if striker else getattr(context, "batter_name", None),
            "batter_id": int(striker.player_id) if striker and striker.player_id is not None else None,
            "non_striker": non_striker.name if non_striker else None,
            "non_striker_id": int(non_striker.player_id) if non_striker and non_striker.player_id is not None else None,
            "bowler": bowler.get("name") or getattr(context, "bowler_name", None),
            "bowler_id": int(bowler.get("player_id")) if bowler.get("player_id") is not None else None,
            "batting_approach": str(getattr(context, "strategy", None) or "Balanced").strip(),
            "bowling_plan": str(session.current_tactic or getattr(context, "bowler_tactic", None) or "Mixed").strip(),
            "pitch": str(session.pitch or "even"),
        }
    )


def _symbol_for(outcome: Any) -> str:
    if bool(outcome.wicket):
        return "W"
    kind = str(outcome.outcome or "dot")
    if kind == "wide":
        return "WD"
    if kind == "no_ball":
        return "NB"
    if kind == "six":
        return "6"
    if kind == "four":
        return "4"
    return str(int(outcome.runs or 0)) if int(outcome.runs or 0) else "•"


def _legacy_ball(result: dict[str, Any], *, batter_before: dict[str, Any] | None, bowler_before: dict[str, Any] | None, over: int, ball: int) -> dict[str, Any]:
    return {
        "innings_number": int(result.get("innings_number") or 1),
        "over": over,
        "ball": ball,
        "legal": bool(result.get("legal_delivery", result.get("outcome") not in {"wide", "no_ball"})),
        "runs": int(result.get("runs") or 0),
        "bowler_runs": int(result.get("runs") or 0),
        "wicket": bool(result.get("wicket")),
        "outcome": str(result.get("outcome") or "dot"),
        "symbol": _legacy_symbol(result),
        "extra_type": result.get("extra_type"),
        "batter": result.get("batter_name"),
        "batter_id": batter_before.get("player_id") if batter_before else None,
        "non_striker": result.get("non_striker_name"),
        "non_striker_id": result.get("non_striker_id"),
        "bowler": result.get("bowler_name"),
        "bowler_id": bowler_before.get("player_id") if bowler_before else None,
        "batting_approach": result.get("stroke_intent") or result.get("intent") or "Neutral",
        "bowling_plan": result.get("delivery_type") or "Mixed",
        "delivery": result.get("delivery_type"),
        "line": result.get("line"),
        "length": result.get("length"),
        "foot": result.get("foot_movement"),
        "stroke_type": result.get("stroke_type"),
        "shot": result.get("specific_shot"),
    }


def _legacy_symbol(result: dict[str, Any]) -> str:
    if result.get("wicket"):
        return "W"
    outcome = str(result.get("outcome") or "dot")
    if outcome == "wide":
        return "WD"
    if outcome == "no_ball":
        return "NB"
    if outcome == "six":
        return "6"
    if outcome in {"four", "boundary"}:
        return "4"
    runs = int(result.get("runs") or 0)
    return str(runs) if runs else "•"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def snapshot_with_analysis(snapshot: dict[str, Any], balls: list[dict[str, Any]], *, source: str | None = None) -> dict[str, Any]:
    out = deepcopy(snapshot)
    out["analysis_balls"] = [deepcopy(x) for x in balls]
    extras = deepcopy(snapshot.get("extras") or {})
    if not extras and balls:
        counts = Counter()
        for ball in balls:
            kind = str(ball.get("extra_type") or ball.get("outcome") or "").lower()
            if kind in {"wide", "wide_ball", "wd"}: counts["wides"] += 1
            elif kind in {"no_ball", "noball", "nb"}: counts["no_balls"] += 1
            elif kind in {"bye", "byes"}: counts["byes"] += 1
            elif kind in {"leg_bye", "legbyes"}: counts["leg_byes"] += 1
            elif kind in {"penalty", "penalty_runs"}: counts["penalty"] += _safe_int(ball.get("runs"))
        extras = dict(counts)
    out["extras"] = extras
    out["wickets_fallen"] = deepcopy(snapshot.get("wickets_fallen") or [])
    out["analysis_overs"] = build_over_rows(balls)
    if source:
        out["source"] = source
    return out


def build_runtime_snapshot(session: Any, snapshotter: Any) -> dict[str, Any]:
    snap = snapshotter(session)
    inning_no = int(getattr(session.innings, "innings_number", snap.get("innings_number") or 1))
    all_balls = list((session.match or {}).get("_analysis_balls") or [])
    balls = [deepcopy(x) for x in all_balls if int(x.get("innings_number") or 1) == inning_no]
    score = getattr(session.innings, "score", None)
    if score is not None:
        snap["extras"] = deepcopy(getattr(score, "extras", {}) or {})
        snap["wickets_fallen"] = deepcopy(getattr(score, "wickets_fallen", []) or [])
    return snapshot_with_analysis(snap, balls, source="runtime")


def build_legacy_snapshot(session: Any, snapshotter: Any, *, first: bool = False) -> dict[str, Any]:
    snap = snapshotter(session)
    if first:
        balls = list((session.meta or {}).get("_analysis_balls_first") or [])
        score = (session.meta or {}).get("_analysis_score_first") or {}
        snap["extras"] = deepcopy(score.get("extras") or {})
        snap["wickets_fallen"] = deepcopy(score.get("wickets_fallen") or [])
    else:
        balls = list((session.meta or {}).get("_analysis_balls") or [])
        scorecard = getattr(session, "score", None)
        snap["extras"] = deepcopy(getattr(scorecard, "extras", {}) or {})
        snap["wickets_fallen"] = deepcopy(getattr(scorecard, "wickets_fallen", []) or [])
    return snapshot_with_analysis(snap, balls, source="legacy")


def build_over_rows(balls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ball in balls:
        groups[_safe_int(ball.get("over")) or 1].append(ball)
    rows = []
    for over_no in sorted(groups):
        events = groups[over_no]
        legal = sum(1 for x in events if x.get("legal"))
        runs = sum(_safe_int(x.get("runs")) for x in events)
        wickets = sum(1 for x in events if x.get("wicket"))
        dots = sum(1 for x in events if x.get("legal") and _safe_int(x.get("runs")) == 0 and not x.get("wicket"))
        fours = sum(1 for x in events if _safe_int(x.get("runs")) == 4 and x.get("outcome") not in {"wide", "no_ball", "bye", "leg_bye"})
        sixes = sum(1 for x in events if _safe_int(x.get("runs")) == 6)
        extras = sum(1 for x in events if x.get("outcome") in {"wide", "no_ball", "bye", "leg_bye", "penalty"})
        batting = Counter(str(x.get("batting_approach") or "Balanced") for x in events if x.get("batting_approach"))
        bowling = Counter(str(x.get("bowling_plan") or "Mixed") for x in events if x.get("bowling_plan"))
        rows.append(
            {
                "over": over_no,
                "runs": runs,
                "wickets": wickets,
                "legal_balls": legal,
                "dots": dots,
                "fours": fours,
                "sixes": sixes,
                "extras": extras,
                "rpo": (runs * 6.0 / legal) if legal else 0.0,
                "balls": [dict(x) for x in events],
                "batting_approach": batting.most_common(1)[0][0] if batting else "Balanced",
                "bowling_plan": bowling.most_common(1)[0][0] if bowling else "Mixed",
            }
        )
    return rows


def phase_name(over: int, total_overs: int = 20) -> str:
    if total_overs <= 1:
        return "Super Over"
    if over <= min(6, total_overs):
        return "Powerplay"
    if over <= max(6, total_overs - 4):
        return "Middle"
    return "Death"


def _par_rpo(innings: list[dict[str, Any]], total_overs: int) -> float:
    first = innings[0] if innings else {}
    runs = _safe_int(first.get("runs"))
    if runs > 0 and total_overs > 0:
        return max(6.0, min(12.5, runs / total_overs))
    observed = []
    for snap in innings:
        for row in snap.get("analysis_overs") or []:
            if row.get("legal_balls"):
                observed.append(float(row.get("rpo") or 0.0))
    return max(6.0, min(12.5, sum(observed) / len(observed))) if observed else 8.5


def _win_probability_for_chase(first_runs: int, score: int, wickets: int, legal_balls: int, total_overs: int = 20) -> float:
    target = max(1, first_runs + 1)
    if score >= target:
        return 100.0
    max_balls = max(1, total_overs * 6)
    balls_left = max(0, max_balls - legal_balls)
    if balls_left <= 0:
        return 0.0
    need = max(0, target - score)
    rrr = need * 6.0 / balls_left
    base = max(6.5, min(13.5, first_runs / total_overs if total_overs else 8.5))
    rate_term = 1.0 / (1.0 + math.exp((rrr - base) / 1.15))
    wicket_term = max(0.45, min(1.08, 0.55 + 0.05 * max(0, 10 - wickets)))
    progress_term = 0.80 + 0.20 * (legal_balls / max_balls)
    return max(0.0, min(100.0, rate_term * wicket_term * progress_term * 100.0))


def derive_analytics(payload: dict[str, Any]) -> dict[str, Any]:
    innings = payload.get("innings") or []
    total_overs = _safe_int(payload.get("overs_limit") or 20)
    if str(payload.get("engine") or "").upper() == "PLAYSO":
        total_overs = 1
    par_rpo = _par_rpo(innings, max(1, total_overs))
    phase_rows = []
    over_rows: list[dict[str, Any]] = []
    momentum_series = []
    pressure_series = []
    win_series = []
    innings_series = []

    for idx, snap in enumerate(innings):
        rows = list(snap.get("analysis_overs") or [])
        cumulative = 0
        for row in rows:
            cumulative += _safe_int(row.get("runs"))
            row2 = deepcopy(row)
            row2["innings_number"] = int(snap.get("innings_number") or idx + 1)
            row2["batting_team_display"] = snap.get("batting_team_display") or "TEAM"
            row2["cumulative_runs"] = cumulative
            phase_rows.append(
                {
                    "innings_number": row2["innings_number"],
                    "team": row2["batting_team_display"],
                    "phase": phase_name(_safe_int(row2.get("over")) or 1, total_overs),
                    "runs": row2["runs"],
                    "wickets": row2["wickets"],
                    "legal_balls": row2["legal_balls"],
                    "rpo": row2["rpo"],
                }
            )
            over_rows.append(row2)
            innings_series.append({
                "innings_number": row2["innings_number"],
                "over": _safe_int(row2.get("over")),
                "team": row2["batting_team_display"],
                "cumulative_runs": cumulative,
            })

        by_phase = defaultdict(lambda: {"runs": 0, "wickets": 0, "balls": 0})
        for row in rows:
            ph = "Super Over" if total_overs == 1 else phase_name(_safe_int(row.get("over")) or 1, total_overs)
            by_phase[ph]["runs"] += _safe_int(row.get("runs"))
            by_phase[ph]["wickets"] += _safe_int(row.get("wickets"))
            by_phase[ph]["balls"] += _safe_int(row.get("legal_balls"))
        for ph, vals in by_phase.items():
            phase_rows.append({
                "innings_number": int(snap.get("innings_number") or idx + 1),
                "team": snap.get("batting_team_display") or "TEAM",
                "phase": ph,
                "runs": vals["runs"],
                "wickets": vals["wickets"],
                "legal_balls": vals["balls"],
                "rpo": vals["runs"] * 6.0 / vals["balls"] if vals["balls"] else 0.0,
            })

    first_runs = _safe_int(innings[0].get("runs")) if innings else 0
    if len(innings) > 1:
        chase = innings[1]
        cumulative = 0
        max_overs = max(1, total_overs)
        for row in chase.get("analysis_overs") or []:
            cumulative += _safe_int(row.get("runs"))
            balls = sum(_safe_int(x.get("legal_balls")) for x in (chase.get("analysis_overs") or []) if _safe_int(x.get("over")) <= _safe_int(row.get("over")))
            wickets = sum(_safe_int(x.get("wickets")) for x in (chase.get("analysis_overs") or []) if _safe_int(x.get("over")) <= _safe_int(row.get("over")))
            p = _win_probability_for_chase(first_runs, cumulative, wickets, balls, max_overs)
            win_series.append({"over": _safe_int(row.get("over")), "probability": round(p, 1)})
    if win_series and payload.get("winner_id") is not None:
        if _safe_int(innings[-1].get("runs")) >= first_runs + 1:
            win_series[-1]["probability"] = 100.0
        else:
            win_series[-1]["probability"] = 0.0

    for row in over_rows:
        rpo = float(row.get("rpo") or 0.0)
        momentum = (rpo - par_rpo) / max(1.0, par_rpo) * 2.0
        if row.get("wickets"):
            momentum -= min(1.25, 0.45 * _safe_int(row.get("wickets")))
        momentum = max(-2.0, min(2.0, momentum))
        momentum_series.append({
            "innings_number": row.get("innings_number"),
            "over": row.get("over"),
            "value": round(momentum, 2),
            "team": row.get("batting_team_display"),
        })
        if int(row.get("innings_number") or 0) == 2:
            p = next((x["probability"] for x in win_series if x["over"] == row["over"]), 0.0) if win_series else 0.0
            pressure = max(0.0, min(2.0, (100.0 - p) / 50.0))
            pressure_series.append({"over": row.get("over"), "value": round(pressure, 2)})

    turning_points = []
    for series_name, series in (("Momentum", momentum_series), ("Win probability", win_series)):
        for prev, cur in zip(series, series[1:]):
            a = float(prev.get("value", prev.get("probability", 0.0)))
            b = float(cur.get("value", cur.get("probability", 0.0)))
            delta = b - a
            if abs(delta) >= (0.7 if series_name == "Momentum" else 10.0):
                turning_points.append({
                    "kind": series_name,
                    "over": cur.get("over"),
                    "change": round(delta, 1),
                    "from": round(a, 1),
                    "to": round(b, 1),
                })
    turning_points.sort(key=lambda x: abs(float(x["change"])), reverse=True)
    turning_points = turning_points[:6]

    player_rows = []
    for snap in innings:
        for bat in snap.get("batters") or []:
            runs = _safe_int(bat.get("runs")); balls = _safe_int(bat.get("balls"))
            sr = runs * 100.0 / balls if balls else 0.0
            impact = min(100.0, runs * 0.65 + _safe_int(bat.get("fours")) * 1.6 + _safe_int(bat.get("sixes")) * 3.0 + min(25.0, sr / 10.0))
            player_rows.append({"name": bat.get("name") or "Player", "team": snap.get("batting_team_display") or "TEAM", "type": "Bat", "runs": runs, "balls": balls, "wickets": 0, "economy": None, "impact": round(impact, 1)})
        for bowl in snap.get("bowlers") or []:
            balls = _safe_int(bowl.get("balls")); runs = _safe_int(bowl.get("runs")); wickets = _safe_int(bowl.get("wickets")); econ = runs * 6.0 / balls if balls else 0.0
            impact = min(100.0, wickets * 28.0 + max(0.0, 16.0 - econ) * 2.5 + balls / 12.0)
            player_rows.append({"name": bowl.get("name") or "Bowler", "team": snap.get("bowling_team_display") or "TEAM", "type": "Bowl", "runs": 0, "balls": balls, "wickets": wickets, "economy": round(econ, 2) if balls else None, "impact": round(impact, 1)})
    player_rows.sort(key=lambda x: x["impact"], reverse=True)

    phase_map = {(r["innings_number"], r["phase"]): r for r in phase_rows if r["runs"] or r["legal_balls"]}
    extras_total = sum(sum(_safe_int(v) for v in (s.get("extras") or {}).values()) for s in innings)
    legal_total = sum(sum(_safe_int(r.get("legal_balls")) for r in s.get("analysis_overs") or []) for s in innings)
    runs_total = sum(_safe_int(s.get("runs")) for s in innings)
    boundaries = sum(_safe_int(b.get("fours")) * 4 + _safe_int(b.get("sixes")) * 6 for s in innings for b in s.get("batters") or [])
    dot_total = sum(_safe_int(r.get("dots")) for r in over_rows)
    ball_total = sum(len(r.get("balls") or []) for r in over_rows)
    single_double_triple = sum(1 for r in over_rows for b in r.get("balls") or [] if b.get("legal") and _safe_int(b.get("runs")) in {1, 2, 3})
    boundary_balls = [b for r in over_rows for b in r.get("balls") or [] if b.get("legal") and _safe_int(b.get("runs")) in {4,6}]
    metrics = {
        "par_rpo": round(par_rpo, 2),
        "extras_total": extras_total,
        "dot_ball_pct": round((dot_total * 100.0 / legal_total), 1) if legal_total else 0.0,
        "boundary_run_pct": round((boundaries * 100.0 / runs_total), 1) if runs_total else 0.0,
        "strike_rotation_pct": round((single_double_triple * 100.0 / legal_total), 1) if legal_total else 0.0,
        "best_over": max(over_rows, key=lambda x: _safe_int(x.get("runs")), default={}),
        "lowest_over": min(over_rows, key=lambda x: _safe_int(x.get("runs")), default={}),
        "max_boundary_streak": _max_boundary_streak([b for r in over_rows for b in r.get("balls") or []]),
        "boundary_balls": len(boundary_balls),
        "legal_balls": legal_total,
        "runs_total": runs_total,
    }

    return {
        "par_rpo": par_rpo,
        "phase_rows": phase_rows,
        "over_rows": over_rows,
        "innings_series": innings_series,
        "momentum_series": momentum_series,
        "pressure_series": pressure_series,
        "win_series": win_series,
        "turning_points": turning_points,
        "player_impact": player_rows[:12],
        "metrics": metrics,
        "phase_map": phase_map,
        "strategy_matrix": _strategy_matrix(innings),
        "strategy_summary": _strategy_summary(innings),
        "strategy_streaks": _strategy_streaks(innings),
        "strategy_combinations": _strategy_combinations(innings),
        "partnerships": _partnerships(innings),
    }


def _max_boundary_streak(balls: list[dict[str, Any]]) -> int:
    best = cur = 0
    for ball in balls:
        if ball.get("legal") and _safe_int(ball.get("runs")) in {4, 6}:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _strategy_matrix(innings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cells = defaultdict(lambda: {"runs": 0, "wickets": 0, "overs": 0})
    for snap in innings:
        for row in snap.get("analysis_overs") or []:
            key = (str(row.get("batting_approach") or "Balanced"), str(row.get("bowling_plan") or "Mixed"))
            cells[key]["runs"] += _safe_int(row.get("runs"))
            cells[key]["wickets"] += _safe_int(row.get("wickets"))
            cells[key]["overs"] += 1
    return [{"batting": k[0], "bowling": k[1], **v, "rpo": round(v["runs"] / v["overs"], 2) if v["overs"] else 0.0} for k, v in cells.items()]


def _strategy_summary(innings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = defaultdict(lambda: {"overs": 0, "runs": 0, "wickets": 0})
    plans = defaultdict(lambda: {"overs": 0, "runs": 0, "wickets": 0})
    for snap in innings:
        for row in snap.get("analysis_overs") or []:
            a = str(row.get("batting_approach") or "Balanced")
            b = str(row.get("bowling_plan") or "Mixed")
            counts[a]["overs"] += 1; counts[a]["runs"] += _safe_int(row.get("runs")); counts[a]["wickets"] += _safe_int(row.get("wickets"))
            plans[b]["overs"] += 1; plans[b]["runs"] += _safe_int(row.get("runs")); plans[b]["wickets"] += _safe_int(row.get("wickets"))
    out = []
    for name, v in sorted(counts.items(), key=lambda kv: (-kv[1]["runs"], kv[0])):
        out.append({"group": "Batting intent", "label": name, **v, "rpo": round(v["runs"] / v["overs"], 2) if v["overs"] else 0.0})
    for name, v in sorted(plans.items(), key=lambda kv: (-kv[1]["runs"], kv[0])):
        out.append({"group": "Bowling plan", "label": name, **v, "rpo": round(v["runs"] / v["overs"], 2) if v["overs"] else 0.0})
    return out




def _strategy_streaks(innings: list[dict[str, Any]]) -> dict[str, Any]:
    longest_batting = {"label": "—", "length": 0}
    longest_bowling = {"label": "—", "length": 0}
    for snap in innings:
        batting_seq = [str(r.get("batting_approach") or "Balanced") for r in (snap.get("analysis_overs") or [])]
        bowling_seq = [str(r.get("bowling_plan") or "Mixed") for r in (snap.get("analysis_overs") or [])]
        for seq, target in ((batting_seq, longest_batting),(bowling_seq,longest_bowling)):
            cur_label = None; cur = 0
            for label in seq:
                if label == cur_label: cur += 1
                else: cur_label, cur = label, 1
                if cur > target["length"]: target.update(label=label, length=cur)
    return {"batting": longest_batting, "bowling": longest_bowling}


def _strategy_combinations(innings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = defaultdict(lambda: {"overs":0,"runs":0,"wickets":0})
    for snap in innings:
        for row in snap.get("analysis_overs") or []:
            key=(str(row.get("batting_approach") or "Balanced"),str(row.get("bowling_plan") or "Mixed"))
            counts[key]["overs"] += 1; counts[key]["runs"] += _safe_int(row.get("runs")); counts[key]["wickets"] += _safe_int(row.get("wickets"))
    rows=[]
    for (bat,bowl), v in counts.items():
        rows.append({"batting":bat,"bowling":bowl,**v,"rpo":round(v["runs"]/v["overs"],2) if v["overs"] else 0.0})
    return sorted(rows,key=lambda x:(-x["runs"],-x["overs"],x["batting"],x["bowling"]))[:10]

def _partnerships(innings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for snap in innings:
        balls = list(snap.get("analysis_balls") or [])
        if not balls:
            continue
        current_pair = None
        runs = 0
        legal = 0
        partnership_no = 0
        for ball in balls:
            pair = (ball.get("batter") or "Player", ball.get("non_striker") or "Player")
            if current_pair is None:
                current_pair = pair
                partnership_no = 1
            runs += _safe_int(ball.get("runs"))
            legal += 1 if ball.get("legal") else 0
            if ball.get("wicket"):
                result.append({
                    "innings_number": snap.get("innings_number"),
                    "team": snap.get("batting_team_display") or "TEAM",
                    "wicket": partnership_no,
                    "partnership": f"{current_pair[0]} & {current_pair[1]}",
                    "runs": runs,
                    "balls": legal,
                    "status": "ended at wicket",
                })
                current_pair = None
                runs = 0
                legal = 0
                partnership_no += 1
        if current_pair is not None:
            result.append({
                "innings_number": snap.get("innings_number"),
                "team": snap.get("batting_team_display") or "TEAM",
                "wicket": partnership_no,
                "partnership": f"{current_pair[0]} & {current_pair[1]}",
                "runs": runs,
                "balls": legal,
                "status": "unbroken",
            })
    return result


def _user_record(match: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "id": _safe_int(match.get(f"{prefix}_id")),
        "username": match.get(f"{prefix}_username"),
        "first_name": match.get(f"{prefix}_name") or match.get(f"{prefix}_first_name") or "Player",
    }


def _team_display_for(match: dict[str, Any], user_id: int) -> str:
    if _safe_int(match.get("challenger_id")) == int(user_id):
        return match.get("challenger_team_name") or match.get("challenger_team_code") or match.get("challenger_name") or "Challenger"
    return match.get("opponent_team_name") or match.get("opponent_team_code") or match.get("opponent_name") or "Opponent"


def _base_payload(*, engine: str, match: dict[str, Any], innings: list[dict[str, Any]], termination: str, winner_id: int | None, loser_id: int | None, ended_by_user_id: int | None, reason: str | None, overs_limit: int = 20) -> dict[str, Any]:
    payload = {
        "engine": str(engine).upper(),
        "game_name": {"PLAY": "Play", "PLAYINT": "PlayInt", "PLAYIPL": "PlayIPL", "PLAYWPL": "PlayWPL", "PLAYSO": "PlaySO", "MATCH": "Match"}.get(str(engine).upper(), str(engine).upper()),
        "match_id": _safe_int(match.get("match_id") or match.get("challenge_id")),
        "chat_id": _safe_int(match.get("chat_id")),
        "format": "T20",
        "overs_limit": overs_limit,
        "stadium": match.get("stadium") or "Virtual Cricket Ground",
        "pitch": match.get("pitch") or "Standard",
        "weather": match.get("weather") or "Not recorded",
        "players": [_user_record(match, "challenger"), _user_record(match, "opponent")],
        "winner_id": int(winner_id) if winner_id is not None else None,
        "loser_id": int(loser_id) if loser_id is not None else None,
        "ended_by_user_id": int(ended_by_user_id) if ended_by_user_id is not None else None,
        "termination": termination,
        "reason": reason or "",
        "toss_winner_id": _safe_int(match.get("toss_winner_id")) or None,
        "toss_winner_name": match.get("toss_winner_name") or None,
        "decision": match.get("decision") or match.get("toss_result"),
        "innings": innings,
    }
    payload["winner_name"] = next((p["first_name"] for p in payload["players"] if payload["winner_id"] and int(p["id"]) == payload["winner_id"]), None)
    if payload.get("toss_winner_id"):
        payload["toss_winner_name"] = next((p["first_name"] for p in payload["players"] if int(p["id"]) == int(payload["toss_winner_id"])), payload.get("toss_winner_name"))
    if not payload["winner_name"]:
        winner_team_id = next((s.get("batting_team_id") for s in innings if s.get("batting_team_id") == winner_id), None) if winner_id else None
        if winner_team_id is not None:
            payload["winner_name"] = _team_display_for(match, _safe_int(winner_team_id))
    payload["potm"] = _pick_potm(innings)
    payload["analytics"] = derive_analytics(payload)
    return payload


def _pick_potm(innings: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for snap in innings:
        for b in snap.get("batters") or []:
            runs = _safe_int(b.get("runs")); balls = _safe_int(b.get("balls"))
            candidates.append((runs * 1.0 + _safe_int(b.get("fours")) * 0.8 + _safe_int(b.get("sixes")) * 1.5 + (runs * 100.0 / balls if balls else 0.0) / 12.0, {"name": b.get("name") or "Player", "runs": runs, "balls": balls, "wickets": 0}))
        for b in snap.get("bowlers") or []:
            wickets = _safe_int(b.get("wickets")); balls = _safe_int(b.get("balls")); runs = _safe_int(b.get("runs"));
            candidates.append((wickets * 30.0 + (12.0 - (runs * 6.0 / balls if balls else 12.0)), {"name": b.get("name") or "Bowler", "runs": 0, "balls": balls, "wickets": wickets}))
    return max(candidates, key=lambda x: x[0])[1] if candidates else {"name": "—", "runs": 0, "balls": 0, "wickets": 0}


def collect_match_row(engine: str, match: dict[str, Any], *, termination: str = "exited", winner_id: int | None = None, loser_id: int | None = None, ended_by_user_id: int | None = None, reason: str | None = None) -> dict[str, Any]:
    """Build a valid report even when an exit happens before live runtime state exists."""
    return _base_payload(
        engine=str(engine).upper(),
        match=dict(match or {}),
        innings=[],
        termination=termination,
        winner_id=winner_id,
        loser_id=loser_id,
        ended_by_user_id=ended_by_user_id,
        reason=reason,
        overs_limit=20,
    )


def collect_runtime_session(engine: str, session: Any, *, termination: str = "completed", winner_id: int | None = None, loser_id: int | None = None, ended_by_user_id: int | None = None, reason: str | None = None) -> dict[str, Any]:
    if str(engine).upper() == "PLAY":
        from engines.play_runtime import snapshot_innings
    elif str(engine).upper() == "PLAYINT":
        from engines.playint_runtime import snapshot_innings
    elif str(engine).upper() == "PLAYIPL":
        from engines.playipl_runtime import snapshot_innings
    else:
        from engines.playwpl_runtime import snapshot_innings
    all_balls = list((session.match or {}).get("_analysis_balls") or [])
    innings = []
    for historical in (session.innings_history or []):
        hn = _safe_int(historical.get("innings_number") or 1)
        innings.append(snapshot_with_analysis(deepcopy(historical), [deepcopy(x) for x in all_balls if _safe_int(x.get("innings_number") or 1) == hn], source="runtime"))
    current = build_runtime_snapshot(session, snapshot_innings)
    if not innings or int(innings[-1].get("innings_number") or 0) != int(current.get("innings_number") or 0):
        innings.append(current)
    else:
        innings[-1] = current
    return _base_payload(
        engine=str(engine).upper(),
        match=dict(session.match or {}),
        innings=innings,
        termination=termination,
        winner_id=winner_id,
        loser_id=loser_id,
        ended_by_user_id=ended_by_user_id,
        reason=reason,
    )


def collect_legacy_session(session: Any, *, termination: str = "completed", winner_id: int | None = None, loser_id: int | None = None, ended_by_user_id: int | None = None, reason: str | None = None) -> dict[str, Any]:
    from services.match_summary import snapshot_normal_session
    innings = []
    first = (session.meta or {}).get("first_innings_card_snapshot")
    if first:
        first = deepcopy(first)
        first_balls = list((session.meta or {}).get("_analysis_balls_first") or [])
        first["analysis_balls"] = first_balls
        first["analysis_overs"] = build_over_rows(first_balls)
        score = (session.meta or {}).get("_analysis_score_first") or {}
        first["extras"] = deepcopy(score.get("extras") or {})
        first["wickets_fallen"] = deepcopy(score.get("wickets_fallen") or [])
        innings.append(first)
    current = build_legacy_snapshot(session, snapshot_normal_session, first=False)
    if not innings or int(innings[-1].get("innings_number") or 0) != int(current.get("innings_number") or 0):
        innings.append(current)
    else:
        innings[-1] = current
    match = {
        "match_id": session.challenge_id or session.match_id,
        "challenge_id": session.challenge_id,
        "challenger_id": session.challenger.user_id,
        "challenger_username": session.challenger.username,
        "challenger_name": session.challenger.first_name,
        "opponent_id": session.opponent.user_id,
        "opponent_username": session.opponent.username,
        "opponent_name": session.opponent.first_name,
        "pitch": (session.meta or {}).get("pitch"),
        "stadium": (session.meta or {}).get("stadium"),
        "weather": (session.meta or {}).get("weather"),
        "format": session.format,
        "chat_id": session.chat_id,
        "toss_winner_id": session.toss_winner_id,
        "decision": session.decision,
    }
    payload = _base_payload(engine="MATCH", match=match, innings=innings, termination=termination, winner_id=winner_id, loser_id=loser_id, ended_by_user_id=ended_by_user_id, reason=reason)
    payload["format"] = session.format or "T20"
    return payload


def collect_playso_match(match: dict[str, Any], state: dict[str, Any], *, termination: str = "completed", winner_id: int | None = None, loser_id: int | None = None, ended_by_user_id: int | None = None, reason: str | None = None) -> dict[str, Any]:
    state = deepcopy(state or {})
    innings = [deepcopy(x) for x in (state.get("innings_history") or [])]
    current_no = _safe_int(state.get("innings_no") or match.get("innings_no") or 1)
    current_snap = {
        "innings_number": current_no,
        "batting_team_id": _safe_int(state.get("batting_user")),
        "bowling_team_id": _safe_int(state.get("bowling_user")),
        "batting_team_display": _team_display_for(match, _safe_int(state.get("batting_user"))),
        "bowling_team_display": _team_display_for(match, _safe_int(state.get("bowling_user"))),
        "runs": _safe_int(state.get("runs")),
        "wickets": _safe_int(state.get("wickets")),
        "legal_balls": _safe_int(state.get("legal_balls")),
        "target": _safe_int(state.get("target")) or None,
        "over_text": f"{_safe_int(state.get('legal_balls')) // 6}.{_safe_int(state.get('legal_balls')) % 6}",
        "batters": list((state.get("batter_stats") or {}).values()),
        "bowlers": list((state.get("bowler_stats") or {}).values()),
        "extras": deepcopy(state.get("extras") or {}),
        "wickets_fallen": deepcopy(state.get("wickets_fallen") or []),
        "analysis_balls": list(state.get("analysis_balls") or []),
    }
    current_snap = snapshot_with_analysis(current_snap, list(current_snap["analysis_balls"]), source="playso")
    if termination != "completed" or not innings or int(innings[-1].get("innings_number") or 0) != current_no:
        innings.append(current_snap)
    elif innings:
        innings[-1] = current_snap
    payload = _base_payload(engine="PLAYSO", match=match, innings=innings, termination=termination, winner_id=winner_id, loser_id=loser_id, ended_by_user_id=ended_by_user_id, reason=reason, overs_limit=1)
    payload["format"] = "Super Over"
    payload["stadium"] = match.get("stadium") or "Super Over Ground"
    payload["weather"] = match.get("weather") or "Not recorded"
    payload["super_over_rounds"] = list(state.get("super_over_rounds") or [])
    return payload


def append_legacy_ball(session: Any, result: dict[str, Any], *, batter_before: dict[str, Any] | None, bowler_before: dict[str, Any] | None) -> None:
    score_before = int(result.get("_score_legal_before") or getattr(session.score, "legal_balls", 0))
    over = int(result.get("over_number") or ((score_before // 6) + 1))
    ball = int(result.get("ball_number") or ((score_before % 6) + 1))
    result["innings_number"] = int(getattr(session.innings, "innings_number", 1) if session.innings else 1)
    result["batter_name"] = batter_before.get("name") if batter_before else result.get("batter_name")
    result["batter_id"] = batter_before.get("player_id") if batter_before else None
    result["bowler_name"] = bowler_before.get("name") if bowler_before else result.get("bowler_name")
    result["bowler_id"] = bowler_before.get("player_id") if bowler_before else None
    result["non_striker_name"] = (session.innings.non_striker.name if session.innings and session.innings.non_striker else None)
    result["non_striker_id"] = (session.innings.non_striker.player_id if session.innings and session.innings.non_striker else None)
    result["legal_delivery"] = result.get("outcome") not in {"wide", "no_ball"}
    result["delivery_type"] = result.get("delivery_type") or session.selected_delivery
    result["line"] = result.get("line") or session.selected_line
    result["length"] = result.get("length") or session.selected_length
    result["foot_movement"] = result.get("foot_movement") or session.selected_foot
    result["stroke_type"] = result.get("stroke_type") or session.selected_stroke_type
    result["stroke_intent"] = result.get("stroke_intent") or session.selected_intent
    result["specific_shot"] = result.get("specific_shot") or session.selected_shot
    session.history.append(result)
    session.meta.setdefault("_analysis_balls", []).append(_legacy_ball(result, batter_before=batter_before, bowler_before=bowler_before, over=over, ball=ball))
