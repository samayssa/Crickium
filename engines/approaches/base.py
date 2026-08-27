"""Shared helpers for approach-based over simulation."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any

OUTCOMES = [
    "dot",
    "single",
    "double",
    "triple",
    "four",
    "six",
    "wicket",
    "wide",
    "no_ball",
    "bye",
    "leg_bye",
]

OUTCOME_RUNS = {
    "dot": 0,
    "single": 1,
    "double": 2,
    "triple": 3,
    "four": 4,
    "six": 6,
    "wicket": 0,
    "wide": 1,
    "no_ball": 1,
    "bye": 1,
    "leg_bye": 1,
}

OUTCOME_SYMBOL = {
    "dot": "0",
    "single": "1",
    "double": "2",
    "triple": "3",
    "four": "4",
    "six": "6",
    "wicket": "W",
    "wide": "wd",
    "no_ball": "nb",
    "bye": "b",
    "leg_bye": "lb",
}


@dataclass(slots=True)
class BallContext:
    strategy: str
    pitch: str
    over_number: int
    ball_number: int
    batter_level: int
    bowler_level: int
    batsman_balls_faced: int
    batsman_runs: int
    total_runs: int
    wickets: int
    bowler_role: str | None = None
    bowler_hand: str | None = None
    batter_role: str | None = None
    batter_name: str | None = None
    bowler_name: str | None = None
    bowler_tactic: str = "swinging"
    confidence: float = 0.0
    wickets_this_over: int = 0
    target: int | None = None
    balls_remaining: int = 120
    wickets_in_hand: int = 10
    batting_position: int = 1
    over_runs: int = 0
    high_run_overs: int = 0
    very_high_run_overs: int = 0


@dataclass(slots=True)
class BallOutcome:
    outcome: str
    runs: int
    legal: bool
    wicket: bool
    extra_type: str | None = None
    batter_runs: int = 0
    bowler_runs: int = 0

    @property
    def symbol(self) -> str:
        return OUTCOME_SYMBOL.get(self.outcome, "0")


def outcome_symbol(outcome: str) -> str:
    return OUTCOME_SYMBOL.get(outcome, "0")


def outcome_runs(outcome: str) -> int:
    return OUTCOME_RUNS.get(outcome, 0)


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _bowler_family(bowler_role: str | None, bowler_hand: str | None) -> str:
    text = _norm(bowler_hand or bowler_role)
    if any(key in text for key in ["spin", "leg", "off", "slow", "orthodox", "chinaman"]):
        return "spin"
    if any(key in text for key in ["pace", "fast", "medium", "seam", "swing", "yorker", "bouncer", "knuckle"]):
        return "pace"
    return "pace"


def confidence_band(balls_faced: int) -> str:
    if balls_faced <= 5:
        return "fresh"
    if balls_faced <= 15:
        return "building"
    if balls_faced <= 30:
        return "set"
    if balls_faced <= 45:
        return "flow"
    return "dominant"


def over_phase(over_number: int) -> str:
    if over_number <= 6:
        return "powerplay"
    if over_number <= 15:
        return "middle"
    return "death"


def apply_delta(weights: dict[str, float], **deltas: float) -> dict[str, float]:
    new = dict(weights)
    for key, delta in deltas.items():
        new[key] = max(0.0, new.get(key, 0.0) + delta)
    return new


def apply_multiplier(weights: dict[str, float], **mults: float) -> dict[str, float]:
    new = dict(weights)
    for key, mult in mults.items():
        new[key] = max(0.0, new.get(key, 0.0) * mult)
    return new


def normalize(weights: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        return {"dot": 1.0}
    return {k: v / total for k, v in clean.items() if v > 0}


def choose(weights: dict[str, float]) -> str:
    outcomes = list(weights.keys())
    values = list(weights.values())
    return random.choices(outcomes, weights=values, k=1)[0]


def pitch_bias(weights: dict[str, float], context: BallContext) -> dict[str, float]:
    pitch = _norm(context.pitch)
    family = _bowler_family(context.bowler_role, context.bowler_hand)

    if pitch == "green":
        weights = apply_delta(weights, dot=+0.06, wicket=+0.06, four=-0.05, six=-0.05, single=-0.02)
        if family == "pace":
            weights = apply_delta(weights, wicket=+0.04, wide=+0.01)
        else:
            weights = apply_delta(weights, wicket=+0.02, dot=+0.02)
    elif pitch == "dry":
        weights = apply_delta(weights, single=+0.04, double=+0.02, four=-0.03, six=-0.03, dot=-0.02)
        if family == "spin":
            weights = apply_delta(weights, wicket=+0.05, dot=+0.02)
    elif pitch == "dusty":
        weights = apply_delta(weights, dot=+0.03, single=+0.03, four=-0.04, six=-0.03)
        if family == "spin":
            weights = apply_delta(weights, wicket=+0.06, dot=+0.03)
    elif pitch == "flat":
        weights = apply_delta(weights, single=+0.04, double=+0.02, four=+0.06, six=+0.03, dot=-0.04, wicket=-0.02)
    elif pitch == "hard":
        weights = apply_delta(weights, dot=+0.02, four=+0.02, wicket=+0.03)
        if family == "pace":
            weights = apply_delta(weights, wicket=+0.03)
    elif pitch == "even":
        weights = apply_delta(weights, single=+0.01, double=+0.01, wicket=+0.01)
    elif pitch == "bouncy":
        weights = apply_delta(weights, dot=+0.03, wicket=+0.05, six=+0.02)
        if family == "pace":
            weights = apply_delta(weights, wicket=+0.03)
    elif pitch == "slow":
        weights = apply_delta(weights, single=+0.06, double=+0.03, four=-0.05, six=-0.06, dot=+0.01)
        if family == "spin":
            weights = apply_delta(weights, wicket=+0.02, dot=+0.02)
    return weights


def phase_bias(weights: dict[str, float], context: BallContext) -> dict[str, float]:
    phase = over_phase(context.over_number)
    if phase == "powerplay":
        weights = apply_delta(weights, single=+0.02, four=+0.03, six=+0.01, wicket=+0.03, dot=-0.01)
    elif phase == "middle":
        weights = apply_delta(weights, single=+0.04, double=+0.02, dot=+0.01, wicket=+0.01)
    else:
        weights = apply_delta(weights, four=+0.06, six=+0.06, wicket=+0.04, dot=-0.04, single=-0.02)
    return weights


def confidence_bias(weights: dict[str, float], context: BallContext) -> dict[str, float]:
    band = confidence_band(context.batsman_balls_faced)
    if band == "fresh":
        weights = apply_delta(weights, dot=+0.04, wicket=+0.03, four=-0.02, six=-0.02, single=-0.01)
    elif band == "building":
        weights = apply_delta(weights, single=+0.03, double=+0.02, dot=+0.01)
    elif band == "set":
        weights = apply_delta(weights, four=+0.03, six=+0.02, dot=-0.02)
    elif band == "flow":
        weights = apply_delta(weights, four=+0.05, six=+0.04, wicket=-0.02)
    else:
        weights = apply_delta(weights, four=+0.07, six=+0.05, wicket=-0.03)
    return weights


def level_bias(weights: dict[str, float], context: BallContext) -> dict[str, float]:
    diff = int(context.batter_level) - int(context.bowler_level)
    if diff >= 30:
        weights = apply_delta(weights, four=+0.08, six=+0.08, wicket=-0.05, dot=-0.03)
    elif diff >= 15:
        weights = apply_delta(weights, four=+0.05, six=+0.05, wicket=-0.03, dot=-0.02)
    elif diff <= -30:
        weights = apply_delta(weights, dot=+0.08, wicket=+0.08, four=-0.08, six=-0.05)
    elif diff <= -15:
        weights = apply_delta(weights, dot=+0.05, wicket=+0.04, four=-0.05, six=-0.03)
    return weights


def normalize_and_choose(weights: dict[str, float]) -> str:
    normalized = normalize(weights)
    return choose(normalized)


def resolve_generic(context: BallContext, base_weights: dict[str, float]) -> BallOutcome:
    weights = dict(base_weights)
    weights = pitch_bias(weights, context)
    weights = phase_bias(weights, context)
    weights = confidence_bias(weights, context)
    weights = level_bias(weights, context)
    outcome = normalize_and_choose(weights)

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



def resolve_outcome(context: BallContext, base_weights: dict[str, float]) -> BallOutcome:
    return resolve_generic(context, base_weights)
