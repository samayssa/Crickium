"""
Shot engine for the cricket match flow.

This module centralizes batting stance, stroke families, shot pools, and
match-up helpers. It is the canonical replacement for the older
deliveries.py data file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DELIVERY_TYPES = [
    "Slower Ball",
    "Yorker Ball",
    "Fast Ball",
    "Knuckle Ball",
    "Outswing Ball",
    "Inswing Ball",
    "Reverse Swing Ball",
    "Bouncer Ball",
]

LINES = [
    "Wide of Off Stump",
    "Middle Stump",
    "Leg Stump",
]

LENGTHS = [
    "Yorker Length",
    "Full Length",
    "Good Length",
    "Short Length",
]

FOOT_MOVEMENTS = [
    "Front Foot",
    "Back Foot",
    "Advance",
]

STROKE_TYPES = [
    "Ground Shot",
    "Lofted Shot",
]

STROKE_INTENTS = [
    "Defensive",
    "Neutral",
    "Attacking",
]

SKIP_LENGTH_FOR = {"Yorker Ball", "Bouncer Ball"}

SHOT_LIBRARY: dict[str, dict[str, dict[str, list[str]]]] = {
    "Front Foot": {
        "Ground Shot": {
            "Pace": [
                "Defence",
                "Push",
                "Cover Drive",
                "Straight Drive",
                "Flick",
                "Square Drive",
                "Late Cut",
            ],
            "Spin": [
                "Defence",
                "Push",
                "Cover Drive",
                "Straight Drive",
                "Flick",
                "Sweep Shot",
                "Reverse Sweep",
            ],
        },
        "Lofted Shot": {
            "Pace": [
                "Down the Ground",
                "Inside-Out Lofted Cover Drive",
                "Lofted Square Drive",
                "Scoop Shot",
                "Slog",
            ],
            "Spin": [
                "Down the Ground",
                "Inside-Out Lofted Cover Drive",
                "Slog Sweep",
                "Paddle Sweep",
                "Switch Hit",
            ],
        },
    },
    "Back Foot": {
        "Ground Shot": {
            "Pace": [
                "Backfoot Defence",
                "Backfoot Punch",
                "Square Cut",
                "Late Cut",
                "Pull Shot",
                "Back Foot Drive",
            ],
            "Spin": [
                "Backfoot Defence",
                "Backfoot Punch",
                "Square Cut",
                "Late Cut",
                "Back Foot Drive",
            ],
        },
        "Lofted Shot": {
            "Pace": [
                "Pull Shot (Lofted)",
                "Lofted Pull Shot",
                "Hook Shot",
                "Lofted Hook Shot",
                "Upper Cut",
                "Ramp/Scoop over Keeper",
                "Lofted Cut",
            ],
            "Spin": [
                "Slog Sweep",
                "Paddle Scoop",
                "Lofted Cut",
                "Switch Hit",
                "Scoop Shot",
            ],
        },
    },
    "Advance": {
        "Ground Shot": {
            "Pace": [
                "Charge + Straight Drive",
                "Charge + Push down the Ground",
                "Charge + On Drive",
            ],
            "Spin": [
                "Charge + Straight Drive",
                "Charge + Cover Drive",
                "Push through the On-side",
            ],
        },
        "Lofted Shot": {
            "Pace": [
                "Charge + Loft over Long-On",
                "Charge + Loft over Cover",
                "Advance + Lofted Drive",
                "Advance + Lofted Straight Drive",
                "Advance + Helicopter Shot",
            ],
            "Spin": [
                "Charge + Loft Straight",
                "Charge + Loft over Long-On",
                "Charge + Loft over Extra Cover",
                "Slog",
                "Advance + Inside-Out Lofted Cover Drive",
            ],
        },
    },
}

def get_length_options(delivery_type: str) -> list[str] | None:
    if delivery_type in SKIP_LENGTH_FOR:
        return None
    return list(LENGTHS)

def get_available_shots(foot_movement: str, stroke_type: str, bowler_type: str) -> list[str]:
    return SHOT_LIBRARY.get(foot_movement, {}).get(stroke_type, {}).get(bowler_type, [])

def get_available_foot_movements() -> list[str]:
    return list(FOOT_MOVEMENTS)

def get_available_stroke_types() -> list[str]:
    return list(STROKE_TYPES)

def get_available_intents() -> list[str]:
    return list(STROKE_INTENTS)

def normalize_choice(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()

def infer_bowler_type(delivery_type: str) -> str:
    pace_like = {
        "Slower Ball",
        "Yorker Ball",
        "Fast Ball",
        "Knuckle Ball",
        "Outswing Ball",
        "Inswing Ball",
        "Reverse Swing Ball",
        "Bouncer Ball",
    }
    return "Pace" if delivery_type in pace_like else "Spin"

@dataclass(slots=True)
class ShotMatchContext:
    delivery_type: str
    line: str
    length: str | None
    foot_movement: str
    stroke_type: str
    shot_name: str
    bowler_type: str

    def normalized(self) -> "ShotMatchContext":
        return ShotMatchContext(
            delivery_type=normalize_choice(self.delivery_type),
            line=normalize_choice(self.line),
            length=normalize_choice(self.length) or None,
            foot_movement=normalize_choice(self.foot_movement),
            stroke_type=normalize_choice(self.stroke_type),
            shot_name=normalize_choice(self.shot_name),
            bowler_type=normalize_choice(self.bowler_type) or "Pace",
        )

def _contains_any(text: str, needles: Iterable[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)

def calculate_shot_match_quality(
    delivery_type: str,
    line: str,
    length: str | None,
    foot_movement: str,
    stroke_type: str,
    shot_name: str,
    bowler_type: str = "Pace",
) -> str:
    ctx = ShotMatchContext(
        delivery_type=delivery_type,
        line=line,
        length=length,
        foot_movement=foot_movement,
        stroke_type=stroke_type,
        shot_name=shot_name,
        bowler_type=bowler_type,
    ).normalized()

    score = 0

    if ctx.foot_movement == "Front Foot":
        if ctx.length and ctx.length in {"Full Length", "Good Length"}:
            score += 2
        elif ctx.length == "Short Length":
            score -= 1
    elif ctx.foot_movement == "Back Foot":
        if ctx.length == "Short Length":
            score += 2
        elif ctx.length in {"Full Length", "Good Length"}:
            score -= 1
    elif ctx.foot_movement == "Advance":
        if ctx.length in {"Full Length", "Good Length"}:
            score += 1
        elif ctx.length == "Short Length":
            score -= 2

    shot = ctx.shot_name.lower()
    if _contains_any(shot, ["cover drive", "straight drive", "defence", "push"]):
        if "off stump" in ctx.line.lower() or "middle" in ctx.line.lower():
            score += 1
        if ctx.length in {"Full Length", "Good Length"}:
            score += 1
    if _contains_any(shot, ["square cut", "late cut", "pull", "hook", "upper cut"]):
        if ctx.length == "Short Length":
            score += 2
        if ctx.delivery_type == "Bouncer Ball" and ctx.foot_movement == "Back Foot":
            score += 1
    if _contains_any(shot, ["sweep", "reverse sweep", "paddle", "switch hit"]):
        if ctx.bowler_type == "Spin":
            score += 2
        else:
            score -= 1
    if _contains_any(shot, ["slog", "loft", "scoop"]):
        score += 0

    if ctx.delivery_type in {"Yorker Ball", "Bouncer Ball"}:
        if ctx.foot_movement == "Front Foot" and _contains_any(shot, ["drive", "defence"]):
            score -= 2
        if ctx.foot_movement == "Back Foot" and _contains_any(shot, ["pull", "hook", "cut", "upper cut"]):
            score += 1

    if score >= 5:
        return "excellent"
    if score >= 3:
        return "good"
    if score >= 1:
        return "neutral"
    if score >= -1:
        return "poor"
    return "terrible"

def shot_category(shot_name: str) -> str:
    shot = shot_name.lower()
    if any(k in shot for k in ["defence", "push"]):
        return "defensive"
    if any(k in shot for k in ["drive"]):
        return "drive"
    if any(k in shot for k in ["cut", "pull", "hook", "upper cut"]):
        return "power"
    if any(k in shot for k in ["sweep", "reverse sweep", "paddle"]):
        return "spin"
    if any(k in shot for k in ["slog", "loft", "scoop", "switch hit"]):
        return "high_risk"
    return "general"

def validate_shot_choice(
    foot_movement: str,
    stroke_type: str,
    bowler_type: str,
    shot_name: str,
) -> bool:
    return shot_name in get_available_shots(foot_movement, stroke_type, bowler_type)
