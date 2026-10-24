from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.playso_probability import probability_distribution, sample_outcome


@dataclass(slots=True)
class BallResult:
    outcome: str
    runs: int
    legal: bool
    wicket: bool
    distribution: dict[str, float] = field(default_factory=dict)


def resolve_ball(*, pitch: str, bowler: dict[str, Any], batter: dict[str, Any],
                 family: str, delivery: str, line: str, length: str,
                 foot: str, intent: str, shot: str) -> BallResult:
    dist = probability_distribution(
        pitch=pitch, family=family, delivery=delivery, line=line, length=length,
        foot=foot, intent=intent, shot=shot,
        batter_level=int(batter.get("bat_level") or 0),
        bowler_level=int(bowler.get("bowl_level") or 0),
    )
    outcome = sample_outcome(dist)
    if outcome == "WIDE":
        return BallResult(outcome, 1, False, False, dist)
    if outcome == "NO_BALL":
        return BallResult(outcome, 1, False, False, dist)
    if outcome in {"LEG_BYE", "BYE"}:
        return BallResult(outcome, 1, True, False, dist)
    if outcome == "OUT":
        return BallResult(outcome, 0, True, True, dist)
    return BallResult(outcome, int(outcome), True, False, dist)
