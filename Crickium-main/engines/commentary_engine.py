"""
Commentary engine for ball-by-ball and stage-by-stage narration.
"""

from __future__ import annotations

import random
from typing import Any

DOT_LINES = [
    "A quiet dot ball. The batter was not quite through the shot.",
    "Straight to the fielder. No run taken.",
    "Beaten by the pace and line. Dot ball.",
]

SINGLE_LINES = [
    "Worked into the gap. Quick single taken.",
    "Soft hands, easy run collected.",
    "Placed neatly for one.",
]

BOUNDARY_LINES = [
    "Cracking shot! That races away to the boundary.",
    "Threaded into the gap and it is four.",
    "Beautiful timing. Four runs added.",
]

SIX_LINES = [
    "That has sailed over the ropes. Six!",
    "Massive strike. The ball is gone for six.",
    "Picked up cleanly and launched into the stands.",
]

WICKET_LINES = [
    "Wicket! The batter has to go.",
    "Got him! A breakthrough for the bowling side.",
    "Massive wicket. The fielding side erupts.",
]

EXTRA_LINES = {
    "wide": [
        "Wide signalled by the umpire.",
        "That drifts beyond control. Wide ball.",
    ],
    "no_ball": [
        "No ball called. Free hit style pressure builds.",
        "The overstep has cost an extra run.",
    ],
    "leg_bye": [
        "Leg bye taken. The ball brushes the pads.",
        "Sneaky leg bye added to the total.",
    ],
    "bye": [
        "Bye signalled. The bat missed but the runs still came.",
        "A bye sneaks through.",
    ],
}

STAGE_LINES = {
    "challenge": [
        "A challenge is on the table.",
        "A new match has been invited.",
    ],
    "toss": [
        "The coin is in the air.",
        "Toss time. The match is balancing on a coin edge.",
    ],
    "decision": [
        "Bat or bowl? The next move matters.",
        "A tactical call will shape the chase.",
    ],
    "innings_start": [
        "The innings begins now.",
        "Players take their marks. Play starts.",
    ],
    "match_end": [
        "The match is over.",
        "A final result has been decided.",
    ],
}

def _pick(lines: list[str]) -> str:
    return random.choice(lines) if lines else ""

MISHIT_LINES = [
    "Miscued that badly, but it stays out of reach of the fielders.",
    "Not the middle of the bat there, a streaky connection.",
    "Mistimed shot, but they get away with it.",
]

def describe_outcome(
    outcome: str,
    runs: int = 0,
    batter_name: str | None = None,
    bowler_name: str | None = None,
    shot_name: str | None = None,
    delivery_name: str | None = None,
    line: str | None = None,
    length: str | None = None,
) -> str:
    outcome = (outcome or "").strip().lower()

    if outcome in {"wide", "no_ball", "leg_bye", "bye"}:
        return describe_extra(outcome)

    if outcome in {"out", "run_out"}:
        core = _pick(WICKET_LINES)
        detail_bits = []
        if delivery_name:
            detail_bits.append(delivery_name)
        if length:
            detail_bits.append(length)
        if line:
            detail_bits.append(line)
        ball_desc = f" ({', '.join(detail_bits)})" if detail_bits else ""
        if batter_name and bowler_name:
            return f"{core} {batter_name} is dismissed by {bowler_name}{ball_desc}."
        if batter_name:
            return f"{core} {batter_name} is dismissed{ball_desc}."
        return core

    if outcome == "six":
        base = _pick(SIX_LINES)
    elif outcome in {"four", "boundary"}:
        base = _pick(BOUNDARY_LINES)
    elif outcome in {"single", "double", "triple"}:
        base = _pick(SINGLE_LINES)
    elif outcome == "mishit":
        base = _pick(MISHIT_LINES)
    else:
        base = _pick(DOT_LINES)

    ball_bits = []
    if delivery_name:
        ball_bits.append(delivery_name)
    if length:
        ball_bits.append(length)
    if line:
        ball_bits.append(line)
    ball_desc = ", ".join(ball_bits)

    detail_bits = []
    if batter_name:
        detail_bits.append(batter_name)
    if shot_name:
        detail_bits.append(f"shot: {shot_name}")
    if ball_desc:
        detail_bits.append(f"ball: {ball_desc}")

    if detail_bits:
        return f"{base} ({' | '.join(detail_bits)})"
    return base

def describe_extra(extra_type: str) -> str:
    lines = EXTRA_LINES.get((extra_type or "").strip().lower(), [])
    return _pick(lines) if lines else "Extra run added."

def describe_ball_result(result: dict[str, Any]) -> str:
    return describe_outcome(
        outcome=result.get("outcome", ""),
        runs=int(result.get("runs", 0) or 0),
        batter_name=result.get("batter_name"),
        bowler_name=result.get("bowler_name"),
        shot_name=result.get("shot_name"),
        delivery_name=result.get("delivery_name"),
        line=result.get("line"),
        length=result.get("length"),
    )

def stage_banner(stage: str, challenger: str | None = None, opponent: str | None = None) -> str:
    lines = STAGE_LINES.get((stage or "").strip().lower(), [])
    base = _pick(lines) if lines else stage.replace("_", " ").title()
    if challenger and opponent:
        return f"{base} {challenger} vs {opponent}"
    return base

def over_summary(runs: int, wickets: int, overs_text: str) -> str:
    return f"After {overs_text} overs: {runs}/{wickets}"

def innings_summary(team_name: str | None, runs: int, wickets: int, overs_text: str, target: int | None = None) -> str:
    if target is None:
        return f"{team_name or 'Team'} finished on {runs}/{wickets} in {overs_text} overs."
    if runs >= target:
        return f"{team_name or 'Team'} chased the target with {runs}/{wickets} in {overs_text} overs."
    return f"{team_name or 'Team'} ended on {runs}/{wickets} in {overs_text} overs, target was {target}."

def match_result_message(winner_name: str | None, loser_name: str | None, margin_text: str) -> str:
    if winner_name and loser_name:
        return f"{winner_name} defeated {loser_name} by {margin_text}."
    if winner_name:
        return f"{winner_name} won the match by {margin_text}."
    return f"Match completed by {margin_text}."
