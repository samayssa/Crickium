"""Strategy resolver for /play - combines bowler tactic + batter
mindset via engines/tactics_engine.py's full probability model."""
from __future__ import annotations

from engines.approaches.base import BallContext, BallOutcome, outcome_runs
from engines.tactics_engine import BATTER_MINDSETS, BOWLER_TACTICS, simulate as tactics_simulate

STRATEGY_LABELS = {
    "defensive": "DEFENSIVE",
    "rotate": "ROTATE",
    "neutral": "NEUTRAL",
    "aggressive": "AGGRESSIVE",
    "ultra_aggressive": "ULTRA AGGRESSIVE",
}

TACTIC_LABELS = {
    "defensive": "DEFENSIVE",
    "swinging": "SWINGING",
    "pace_up": "PACE UP",
    "back_of_length": "BACK OF LENGTH",
    "variation": "VARIATION",
    "off_break": "OFF BREAK BALL",
    "doosra": "DOOSRA BALL",
    "arm_ball": "ARM BALL",
    "carrom_ball": "CARROM BALL",
    "top_spin": "TOP SPIN BALL",
    "leg_breaker": "LEG BREAKER BALL",
    "top_spinner": "TOP SPINNER BALL",
    "slider": "SLIDER BALL",
    "flipper": "FLIPPER BALL",
    "googly_ball": "GOOGLY BALL",
}

# Outcome codes from tactics_engine -> the rest of the project's
# outcome vocabulary (used by engines/innings_engine.py and
# engines/score_engine.py).
_CODE_TO_OUTCOME = {
    0: "dot", 1: "single", 2: "double", 3: "triple", 4: "four", 6: "six",
    "W": "wicket", "WD": "wide", "NB": "no_ball", "LB": "leg_bye", "BY": "bye",
}


def resolve(strategy: str, context: BallContext) -> BallOutcome:
    mindset = str(strategy).strip().lower()
    if mindset not in BATTER_MINDSETS:
        mindset = "neutral"
    tactic = str(context.bowler_tactic or "swinging").strip().lower()
    if tactic not in BOWLER_TACTICS:
        tactic = "swinging"

    code = tactics_simulate(
        tactic, mindset, context.pitch, context.over_number,
        context.batter_level, context.bowler_level, context.confidence,
        batsman_balls_faced=context.batsman_balls_faced,
        wickets_this_over=getattr(context, "wickets_this_over", 0),
        bowler_style=context.bowler_hand,
        bowler_role=context.bowler_role,
    )
    outcome = _CODE_TO_OUTCOME.get(code, "dot")

    legal = outcome not in {"wide", "no_ball"}
    wicket = outcome == "wicket"
    runs = outcome_runs(outcome)
    batter_runs = 0 if outcome in {"dot", "wicket", "wide", "no_ball", "bye", "leg_bye"} else runs
    bowler_runs = runs if outcome not in {"bye", "leg_bye"} else 0
    extra_type = outcome if outcome in {"wide", "no_ball", "bye", "leg_bye"} else None

    return BallOutcome(
        outcome=outcome,
        runs=runs,
        legal=legal,
        wicket=wicket,
        extra_type=extra_type,
        batter_runs=batter_runs,
        bowler_runs=bowler_runs,
    )


def strategy_label(strategy: str) -> str:
    return STRATEGY_LABELS.get(str(strategy).strip().lower(), str(strategy).upper())


def tactic_label(tactic: str) -> str:
    return TACTIC_LABELS.get(str(tactic).strip().lower(), str(tactic).upper())


def available_strategies() -> list[str]:
    return list(BATTER_MINDSETS)


def available_tactics() -> list[str]:
    return list(BOWLER_TACTICS)
