"""Strategy resolver for /play - combines bowler tactic + batter
mindset via engines/tactics_engine.py's full probability model."""
from __future__ import annotations

import random
from typing import Any

from engines.approaches.base import BallContext, BallOutcome, outcome_runs
from engines.tactics_engine import (
    BATTER_MINDSETS, BOWLER_TACTICS, FAST_BOWLER_STYLES, LEG_SPIN_STYLES,
    OFF_SPIN_STYLES, SPINNER_TACTIC_ARCHETYPE, bowler_family, simulate as tactics_simulate,
)

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
    "W": "wicket", "RO": "run_out", "WD": "wide", "NB": "no_ball", "LB": "leg_bye", "BY": "bye",
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
        batter_role=getattr(context, "batter_role", None),
        target=getattr(context, "target", None),
        total_runs=getattr(context, "total_runs", 0),
        balls_remaining=getattr(context, "balls_remaining", 120),
        wickets_in_hand=getattr(context, "wickets_in_hand", 10),
        batting_position=getattr(context, "batting_position", 1),
        over_runs=getattr(context, "over_runs", 0),
        high_run_overs=getattr(context, "high_run_overs", 0),
        very_high_run_overs=getattr(context, "very_high_run_overs", 0),
        free_hit_next_ball=bool(getattr(context, "free_hit_next_ball", False)),
        batting_upgrade_context=getattr(context, "batting_upgrade_context", None),
        bowling_upgrade_context=getattr(context, "bowling_upgrade_context", None),
    )
    outcome = _CODE_TO_OUTCOME.get(code, "dot")

    legal = outcome not in {"wide", "no_ball"}
    wicket = outcome in {"wicket", "run_out"}
    runs = outcome_runs(outcome)
    batter_runs = 0 if outcome in {"dot", "wicket", "run_out", "wide", "no_ball", "bye", "leg_bye"} else runs
    bowler_runs = runs if outcome not in {"bye", "leg_bye"} else 0
    extra_type = outcome if outcome in {"wide", "no_ball", "bye", "leg_bye"} else None

    dismissal_type = None
    dismissal_text = None
    bowler_credited = True
    if outcome == "run_out":
        dismissal_type = "run out"
        bowler_credited = False
    elif wicket:
        style = str(context.bowler_hand or "").strip().upper().replace(" ", "")
        role = str(context.bowler_role or "").strip().lower()
        explicit_pace = style in FAST_BOWLER_STYLES or any(
            token in style or token in role for token in ("FAST", "MEDIUM", "SEAM", "PACE")
        )
        explicit_spin = (
            style in OFF_SPIN_STYLES or style in LEG_SPIN_STYLES
            or any(token in style or token in role for token in ("SPIN", "ORTHODOX", "CHINAMAN", "OFFBREAK", "LEG-BREAK"))
        )
        # Player style/role is the strongest signal; when older squads omit
        # that metadata, the selected specialist spinner delivery is the fallback.
        family = "pace" if explicit_pace else "spin" if explicit_spin or tactic in SPINNER_TACTIC_ARCHETYPE else bowler_family(context.bowler_hand, context.bowler_role)
        if family == "spin":
            dismissal_type = random.choices(
                ["caught", "bowled", "lbw", "stumped"], weights=[54, 22, 19, 5], k=1
            )[0]
        else:
            # Stumping is intentionally excluded for pace and medium-pace bowlers.
            dismissal_type = random.choices(
                ["caught", "bowled", "lbw"], weights=[62, 22, 16], k=1
            )[0]

    return BallOutcome(
        outcome=outcome,
        runs=runs,
        legal=legal,
        wicket=wicket,
        extra_type=extra_type,
        batter_runs=batter_runs,
        bowler_runs=bowler_runs,
        dismissal_type=dismissal_type,
        dismissal_text=dismissal_text,
        bowler_credited=bowler_credited,
    )


def decorate_dismissal(session: Any, outcome: BallOutcome) -> BallOutcome:
    """Attach a realistic scorecard dismissal and, when relevant, a fielder.

    The selected dismissal and fielding name are recorded with the ball so the
    Mini App, Telegram scorecard, and completed report all show the same detail.
    """
    if not outcome.wicket:
        return outcome
    kind = str(outcome.dismissal_type or ("run out" if outcome.outcome == "run_out" else "caught")).lower()
    bowler = getattr(session, "current_bowler", None) or {}
    bowler_name = str(bowler.get("name") or "Bowler") if isinstance(bowler, dict) else "Bowler"
    squad = []
    full_squads = getattr(session, "full_squads", None) or {}
    team_id = getattr(session, "bowling_team_id", None)
    if isinstance(full_squads, dict) and team_id is not None:
        squad = full_squads.get(team_id) or full_squads.get(str(team_id)) or []
    if not squad:
        squad = getattr(session, "bowling_squad", None) or getattr(session, "bowling_pool", None) or []
    fielders = [dict(player) for player in squad if isinstance(player, dict) and str(player.get("name") or "").strip()]
    if kind == "stumped":
        keepers = [player for player in fielders if any(token in str(player.get("role") or "").lower() for token in ("keeper", "wk"))]
        candidate_pool = keepers or fielders
    else:
        candidate_pool = fielders
    fielder = random.choice(candidate_pool) if candidate_pool else {}
    fielder_name = str(fielder.get("name") or "Fielder") if kind in {"caught", "stumped", "run out"} else None

    if kind == "caught":
        text = f"c {fielder_name} b {bowler_name}"
    elif kind == "bowled":
        text = f"b {bowler_name}"
    elif kind == "lbw":
        text = f"lbw b {bowler_name}"
    elif kind == "stumped":
        text = f"st {fielder_name} b {bowler_name}"
    else:
        text = f"run out ({fielder_name})"
        outcome.bowler_credited = False

    outcome.dismissal_type = kind
    outcome.fielder_name = fielder_name
    outcome.dismissal_text = text
    return outcome


def strategy_label(strategy: str) -> str:
    return STRATEGY_LABELS.get(str(strategy).strip().lower(), str(strategy).upper())


def tactic_label(tactic: str) -> str:
    return TACTIC_LABELS.get(str(tactic).strip().lower(), str(tactic).upper())


def available_strategies() -> list[str]:
    return list(BATTER_MINDSETS)


def available_tactics() -> list[str]:
    return list(BOWLER_TACTICS)
