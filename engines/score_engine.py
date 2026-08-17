"""
Score engine for the cricket match flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EXTRA_TYPES = {"wide", "no_ball", "leg_bye", "bye", "penalty"}

@dataclass(slots=True)
class ScoreCard:
    runs: int = 0
    wickets: int = 0
    legal_balls: int = 0
    overs: int = 0
    balls: int = 0
    extras: dict[str, int] = field(default_factory=lambda: {
        "wide": 0,
        "no_ball": 0,
        "leg_bye": 0,
        "bye": 0,
        "penalty": 0,
    })
    wickets_fallen: list[dict[str, Any]] = field(default_factory=list)

    @property
    def over_text(self) -> str:
        return f"{self.overs}.{self.balls}"

def create_score_state() -> ScoreCard:
    return ScoreCard()

def format_over(legal_balls: int) -> str:
    overs = legal_balls // 6
    balls = legal_balls % 6
    return f"{overs}.{balls}"

def is_boundary(runs: int) -> bool:
    return runs in {4, 6}

def should_rotate_strike(runs: int) -> bool:
    return runs % 2 == 1

def register_extra(state: ScoreCard, extra_type: str, runs: int = 1) -> None:
    extra_type = extra_type.strip().lower()
    if extra_type not in EXTRA_TYPES:
        raise ValueError(f"Unknown extra type: {extra_type}")
    state.extras[extra_type] = state.extras.get(extra_type, 0) + runs
    state.runs += runs

def register_wicket(
    state: ScoreCard,
    batter_name: str | None = None,
    wicket_type: str = "caught",
    fielder_name: str | None = None,
) -> None:
    state.wickets += 1
    state.wickets_fallen.append(
        {
            "batter": batter_name,
            "type": wicket_type,
            "fielder": fielder_name,
            "over": state.over_text,
        }
    )

def _apply_legal_delivery(state: ScoreCard) -> None:
    state.legal_balls += 1
    state.overs = state.legal_balls // 6
    state.balls = state.legal_balls % 6

def _normalize_outcome(outcome: str) -> str:
    return str(outcome or "").strip().lower()

def update_score_from_ball(
    state: ScoreCard,
    outcome: str,
    runs: int = 0,
    wicket: bool = False,
    batter_name: str | None = None,
    wicket_type: str = "caught",
    fielder_name: str | None = None,
    extra_type: str | None = None,
    legal_delivery: bool = True,
) -> ScoreCard:
    outcome = _normalize_outcome(outcome)
    is_extra = bool(extra_type) or outcome in {"wide", "no_ball", "leg_bye", "bye", "penalty"}

    if is_extra:
        # Extras are scored ONCE here. They must never also fall through
        # to the normal-runs block below - that was double-counting every
        # wide/no-ball/bye/leg-bye onto the total (e.g. a single wide
        # added 2 runs instead of 1), which is what was throwing off the
        # score/over math whenever extras came up.
        register_extra(state, extra_type or outcome, runs=max(1, runs))
    elif wicket or outcome == "out":
        register_wicket(state, batter_name=batter_name, wicket_type=wicket_type, fielder_name=fielder_name)
    else:
        score_runs = runs
        if outcome == "single":
            score_runs = 1 if runs == 0 else runs
        elif outcome == "double":
            score_runs = 2 if runs == 0 else runs
        elif outcome == "triple":
            score_runs = 3 if runs == 0 else runs
        elif outcome == "four":
            score_runs = 4 if runs == 0 else runs
        elif outcome == "five":
            score_runs = 5 if runs == 0 else runs
        elif outcome == "six":
            score_runs = 6 if runs == 0 else runs
        elif outcome == "dot":
            score_runs = 0 if runs == 0 else runs

        if score_runs > 0:
            state.runs += score_runs

    if legal_delivery and outcome not in {"wide", "no_ball"} and not (extra_type in {"wide", "no_ball"}):
        _apply_legal_delivery(state)

    return state

def ball_summary(state: ScoreCard) -> str:
    return f"{state.runs}/{state.wickets} in {format_over(state.legal_balls)} overs"

def innings_finished(state: ScoreCard, max_wickets: int = 10, max_legal_balls: int = 120, target: int | None = None) -> bool:
    if state.wickets >= max_wickets:
        return True
    if state.legal_balls >= max_legal_balls:
        return True
    if target is not None and state.runs >= target:
        return True
    return False
