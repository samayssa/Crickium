"""
Probability engine for the cricket bot.

This module now follows the full blueprint more closely:
- bowler and batsman levels
- batsman experience tiers
- delivery type
- line
- length
- foot movement
- stroke type
- stroke intent
- specific shot
- bowler type (pace/spin)

It keeps backward compatibility with the earlier API used by the bot.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import random
import re
from typing import Any

from database.probability_profiles_repo import load_probability_profiles

OUTCOMES = ["dot", "single", "boundary", "six", "out", "mishit"]

# ---------------------------------------------------------------------
# Base probability models
# ---------------------------------------------------------------------

BASE_PROBS: dict[tuple[str, str], dict[str, float]] = {
    ("Ground Shot", "Defensive"): {
        "dot": 0.45,
        "single": 0.25,
        "boundary": 0.15,
        "six": 0.00,
        "out": 0.15,
        "mishit": 0.00,
    },
    ("Ground Shot", "Neutral"): {
        "dot": 0.30,
        "single": 0.30,
        "boundary": 0.25,
        "six": 0.00,
        "out": 0.10,
        "mishit": 0.05,
    },
    ("Ground Shot", "Attacking"): {
        "dot": 0.20,
        "single": 0.20,
        "boundary": 0.35,
        "six": 0.05,
        "out": 0.15,
        "mishit": 0.05,
    },
    ("Lofted Shot", "Defensive"): {
        "dot": 0.35,
        "single": 0.20,
        "boundary": 0.15,
        "six": 0.05,
        "out": 0.20,
        "mishit": 0.05,
    },
    ("Lofted Shot", "Neutral"): {
        "dot": 0.20,
        "single": 0.25,
        "boundary": 0.30,
        "six": 0.15,
        "out": 0.10,
        "mishit": 0.05,
    },
    ("Lofted Shot", "Attacking"): {
        "dot": 0.15,
        "single": 0.20,
        "boundary": 0.35,
        "six": 0.20,
        "out": 0.10,
        "mishit": 0.05,
    },
}

EXPERIENCE_MODIFIERS: dict[str, dict[str, float]] = {
    "Fresh": {"boundary": -0.15, "out": +0.20, "dot": +0.10},
    "Settling": {"boundary": 0.0, "out": 0.0, "dot": 0.0},
    "Set": {"boundary": +0.25, "out": -0.10, "dot": -0.15},
}

INTENT_MODIFIERS: dict[str, dict[str, float]] = {
    "Defensive": {"dot": +0.20, "boundary": -0.30, "out": -0.15},
    "Neutral": {},
    "Attacking": {"boundary": +0.30, "six": +0.20, "out": +0.15},
}

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

LINES = ["Wide of Off Stump", "Middle Stump", "Leg Stump"]
LENGTHS = ["Yorker Length", "Full Length", "Good Length", "Short Length"]
FOOT_MOVEMENTS = ["Front Foot", "Back Foot", "Advance"]
STROKE_TYPES = ["Ground Shot", "Lofted Shot"]
STROKE_INTENTS = ["Defensive", "Neutral", "Attacking"]

SKIP_LENGTH_FOR = {"Yorker Ball", "Bouncer Ball"}

# Some canonical shot names. We keep them as readable text because the
# UI shows them directly.
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
            ],
            "Spin": [
                "Backfoot Defence",
                "Backfoot Punch",
                "Square Cut",
                "Late Cut",
            ],
        },
        "Lofted Shot": {
            "Pace": [
                "Pull Shot (Lofted)",
                "Hook Shot",
                "Upper Cut",
                "Ramp/Scoop over Keeper",
            ],
            "Spin": [
                "Slog Sweep",
                "Paddle Scoop",
                "Lofted Cut",
                "Switch Hit",
            ],
        },
    },
    "Advance": {
        "Ground Shot": {
            "Pace": [
                "Charge + Straight Drive",
                "Charge + Push down the Ground",
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
            ],
            "Spin": [
                "Charge + Loft Straight",
                "Charge + Loft over Long-On",
                "Charge + Loft over Extra Cover",
                "Slog",
            ],
        },
    },
}

# Shot families for match quality inference
SHOT_KEYWORDS = {
    "defence": "defence",
    "defense": "defence",
    "push": "push",
    "cover drive": "drive",
    "straight drive": "drive",
    "square drive": "drive",
    "flick": "flick",
    "square cut": "cut",
    "late cut": "cut",
    "cut": "cut",
    "pull": "pull",
    "hook": "hook",
    "upper cut": "upper_cut",
    "reverse sweep": "sweep",
    "sweep": "sweep",
    "slog sweep": "slog",
    "slog": "slog",
    "scoop": "scoop",
    "ramp": "scoop",
    "loft": "loft",
    "inside-out": "drive",
    "inside out": "drive",
    "charge": "advance",
    "down the ground": "drive",
    "backfoot defence": "defence",
    "backfoot punch": "push",
    "backfoot": "backfoot",
}

SHOT_PROFILE_MODIFIERS: dict[str, dict[str, float]] = {
    "defence": {"dot": +0.12, "single": +0.08, "boundary": -0.10, "six": -0.03, "out": -0.07},
    "push": {"dot": +0.05, "single": +0.10, "boundary": -0.03, "out": -0.02},
    "drive": {"dot": -0.05, "single": +0.02, "boundary": +0.10, "six": +0.02, "out": -0.03},
    "cut": {"dot": -0.02, "single": +0.02, "boundary": +0.12, "six": +0.00, "out": -0.01},
    "flick": {"dot": -0.03, "single": +0.05, "boundary": +0.06, "out": -0.02},
    "pull": {"dot": -0.02, "single": +0.03, "boundary": +0.12, "six": +0.08, "out": +0.01},
    "hook": {"dot": -0.03, "single": +0.01, "boundary": +0.10, "six": +0.06, "out": +0.03},
    "upper_cut": {"dot": -0.01, "single": +0.01, "boundary": +0.08, "six": +0.04, "out": +0.04},
    "sweep": {"dot": -0.02, "single": +0.02, "boundary": +0.08, "six": +0.04, "out": +0.02},
    "slog": {"dot": -0.08, "single": -0.03, "boundary": +0.10, "six": +0.12, "out": +0.08},
    "scoop": {"dot": -0.06, "single": -0.02, "boundary": +0.06, "six": +0.08, "out": +0.06},
    "loft": {"dot": -0.05, "single": -0.02, "boundary": +0.10, "six": +0.10, "out": +0.05},
    "advance": {"dot": -0.03, "single": +0.03, "boundary": +0.08, "six": +0.05, "out": +0.02},
    "backfoot": {"dot": +0.02, "single": +0.03, "boundary": +0.04, "out": -0.01},
}

# ---------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------

@dataclass(slots=True)
class BallContext:
    bowler_level: int
    batsman_level: int
    balls_faced: int = 0
    delivery_type: str | None = None
    line: str | None = None
    length: str | None = None
    foot_movement: str | None = None
    stroke_type: str | None = None
    stroke_intent: str | None = None
    specific_shot: str | None = None
    bowler_is_pace_or_spin: str | None = None
    batsman_experience: str | None = None
    shot_match_quality: str | None = None
    over_number: int | None = None
    pitch: str | None = None
    approach: str | None = None


# ---------------------------------------------------------------------
# Uploaded probability profile cache
# ---------------------------------------------------------------------

PROFILE_OUTCOME_KEY_MAP = {
    "DOT": "dot",
    "SINGLE": "single",
    "DOUBLE": "double",
    "TRIPLE": "triple",
    "FOUR": "four",
    "FIVE": "five",
    "SIX": "six",
    "WIDE": "wide",
    "NO_BALL": "no_ball",
    "LEG_BYE": "leg_bye",
    "BYE": "bye",
    "OUT": "out",
    "RUN_OUT": "run_out",
}

PROBABILITY_PROFILE_CACHE: list[dict[str, Any]] = []


def clear_probability_profile_cache() -> None:
    global PROBABILITY_PROFILE_CACHE
    PROBABILITY_PROFILE_CACHE = []


async def reload_probability_profile_cache() -> list[dict[str, Any]]:
    global PROBABILITY_PROFILE_CACHE
    rows = await load_probability_profiles()
    PROBABILITY_PROFILE_CACHE = [dict(row) for row in rows]
    return PROBABILITY_PROFILE_CACHE


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _normalize_profile_selector(value: Any) -> str:
    return _normalize_text(value).replace("-", "_").replace(" ", "_")


def _profile_selector_matches_context(selectors: dict[str, Any], context: BallContext) -> bool:
    def _range_ok(min_key: str, max_key: str, current: int | None) -> bool:
        if current is None:
            return True
        low = int(selectors.get(min_key, 0) or 0)
        high = int(selectors.get(max_key, 0) or 0)
        return low <= current <= high

    if not _range_ok("bat_min", "bat_max", int(context.batsman_level or 0)):
        return False
    if not _range_ok("bowl_min", "bowl_max", int(context.bowler_level or 0)):
        return False
    if not _range_ok("balls_faced_min", "balls_faced_max", int(context.balls_faced or 0)):
        return False
    if not _range_ok("over_min", "over_max", int(getattr(context, "over_number", 0) or 0)):
        return False

    if getattr(context, "pitch", None) is not None:
        if _normalize_profile_selector(selectors.get("pitch")) != _normalize_profile_selector(context.pitch):
            return False

    comparisons = {
        "delivery": context.delivery_type,
        "line": context.line,
        "length": context.length,
        "movement": context.foot_movement,
        "approach": getattr(context, "approach", None),
        "shot": context.specific_shot,
    }

    for key, actual in comparisons.items():
        if actual is None:
            continue
        sel = _normalize_profile_selector(selectors.get(key))
        act = _normalize_profile_selector(actual)
        if key == "delivery":
            if sel not in act and act not in sel and not {sel, act} & {sel.replace("_BALL", ""), act.replace("_BALL", "")}:
                return False
        elif key == "line":
            line_map = {
                "WST": {"WST", "WIDE_OF_OFF_STUMP", "WIDE_OF_OFF_STUMPS", "WIDE_OF_OFF"},
                "MST": {"MST", "MIDDLE_STUMP", "MIDDLE_OF_STUMP"},
                "LST": {"LST", "LEG_STUMP", "LEG_OF_STUMP"},
            }
            sel_group = line_map.get(sel, {sel})
            act_group = line_map.get(act, {act})
            if not (sel_group & act_group or sel in act or act in sel):
                return False
        elif key == "length":
            length_map = {
                "FL": {"FL", "FULL_LENGTH"},
                "GL": {"GL", "GOOD_LENGTH"},
                "SL": {"SL", "SHORT_LENGTH", "SHORT_OF_LENGTH"},
                "YL": {"YL", "YORKER_LENGTH", "YORKER"},
            }
            sel_group = length_map.get(sel, {sel})
            act_group = length_map.get(act, {act})
            if not (sel_group & act_group or sel in act or act in sel):
                return False
        elif key == "movement":
            move_map = {
                "FRONT": {"FRONT", "FRONT_FOOT"},
                "BACK": {"BACK", "BACK_FOOT"},
                "ADVANCE": {"ADVANCE", "STEP_OUT", "STEPOUT"},
            }
            sel_group = move_map.get(sel, {sel})
            act_group = move_map.get(act, {act})
            if not (sel_group & act_group or sel in act or act in sel):
                return False
        elif key == "approach":
            approach_map = {
                "GROUND": {"GROUND", "GROUNDED", "GROUND_SHOT"},
                "LOFT": {"LOFT", "LOFTED", "LOFTED_SHOT"},
                "ADVANCE": {"ADVANCE", "STEP_OUT", "STEPOUT"},
            }
            sel_group = approach_map.get(sel, {sel})
            act_group = approach_map.get(act, {act})
            if not (sel_group & act_group or sel in act or act in sel):
                return False
        else:
            if sel != act and sel not in act and act not in sel:
                return False

    return True


def _profile_tier_index(context: BallContext) -> int:
    quality = _normalize_text(context.shot_match_quality or "neutral")
    base_map = {"terrible": 0, "poor": 2, "neutral": 4, "good": 7, "excellent": 9}
    base = base_map.get(quality, 4)
    skill_bias = int(round(max(-2.0, min(2.0, (context.batsman_level - context.bowler_level) / 10.0))))
    exp_bias = {"Fresh": -1, "Settling": 0, "Set": 1}.get(str(context.batsman_experience or "").title(), 0)
    ball_bias = 1 if int(context.balls_faced or 0) >= 15 else 0
    return max(0, min(9, base + skill_bias + exp_bias + ball_bias))


def _profile_probability_weights(context: BallContext) -> dict[str, float] | None:
    if not PROBABILITY_PROFILE_CACHE:
        return None

    candidate = None
    for profile in PROBABILITY_PROFILE_CACHE:
        selectors = _jsonish(profile.get("selectors")) or {}
        if isinstance(selectors, str):
            try:
                selectors = json.loads(selectors)
            except Exception:
                selectors = {}
        if not isinstance(selectors, dict):
            continue
        if _profile_selector_matches_context(selectors, context):
            candidate = profile
            break

    if candidate is None:
        return None

    probabilities = _jsonish(candidate.get("probabilities")) or _jsonish(candidate.get("outcomes")) or {}
    if isinstance(probabilities, str):
        try:
            probabilities = json.loads(probabilities)
        except Exception:
            probabilities = {}
    if not isinstance(probabilities, dict) or not probabilities:
        return None

    tier = _profile_tier_index(context)
    weights: dict[str, float] = {}
    for raw_key in PROBABILITY_KEYS:
        values = probabilities.get(raw_key, [])
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except Exception:
                values = []
        if not isinstance(values, list) or not values:
            continue
        idx = min(tier, len(values) - 1)
        value = float(values[idx])
        outcome = PROFILE_OUTCOME_KEY_MAP.get(raw_key)
        if outcome is not None and value > 0:
            weights[outcome] = value

    if not weights:
        return None

    total = sum(weights.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in weights.items()}


# ---------------------------------------------------------------------
# Normalization and mapping helpers
# ---------------------------------------------------------------------

def _normalize_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _canonical_stroke_type(stroke_type: str | None) -> str:
    text = _normalize_text(stroke_type)
    if text in {"ground", "ground shot", "defence", "defense"}:
        return "Ground Shot"
    if text in {"lofted", "lofted shot"}:
        return "Lofted Shot"
    return "Ground Shot" if "ground" in text else "Lofted Shot" if "loft" in text else "Ground Shot"


def _canonical_stroke_intent(stroke_intent: str | None) -> str:
    text = _normalize_text(stroke_intent)
    if text in {"defensive", "defence", "defense"}:
        return "Defensive"
    if text in {"attacking", "attack"}:
        return "Attacking"
    return "Neutral"


def _canonical_bowler_type(bowler_is_pace_or_spin: str | None, delivery_type: str | None = None) -> str:
    text = _normalize_text(bowler_is_pace_or_spin)
    if text in {"pace", "fast"}:
        return "Pace"
    if text in {"spin"}:
        return "Spin"

    # Infer from delivery if not explicitly supplied.
    d = _normalize_text(delivery_type)
    if any(x in d for x in ["inswing", "outswing", "reverse swing", "knuckle", "bouncer", "fast", "slower", "yorker"]):
        # Most cricket game engines treat these as pace-family deliveries.
        return "Pace"
    return "Spin"


def experience_tier(balls_faced: int, batsman_experience: str | None = None) -> str:
    if batsman_experience:
        text = _normalize_text(batsman_experience)
        if text in {"fresh", "settling", "set"}:
            return text.capitalize() if text != "set" else "Set"
    if balls_faced <= 0:
        return "Fresh"
    if balls_faced < 10:
        return "Settling"
    return "Set"


def skill_diff_modifier(bowler_level: int, batsman_level: int) -> dict[str, float]:
    diff = bowler_level - batsman_level
    if diff >= 20:
        return {"out": +0.25, "single": -0.10, "boundary": -0.20}
    if diff >= 5:
        return {"out": +0.10, "boundary": -0.05}
    if diff <= -20:
        return {"out": -0.25, "boundary": +0.30, "six": +0.15}
    if diff <= -5:
        return {"out": -0.10, "boundary": +0.10}
    return {}


def shot_match_modifier(match_quality: str) -> dict[str, float]:
    table: dict[str, dict[str, float]] = {
        "excellent": {"boundary_mult": 1.4, "six_mult": 1.3, "out": -0.15},
        "good": {"boundary_mult": 1.2, "six_mult": 1.1, "out": -0.05},
        "neutral": {"boundary_mult": 1.0, "six_mult": 1.0, "out": 0.0},
        "poor": {"boundary_mult": 0.5, "six_mult": 0.2, "out": +0.25},
        "terrible": {"boundary_mult": 0.25, "six_mult": 0.05, "out": +0.45},
    }
    return table.get(_normalize_text(match_quality), table["neutral"])


def get_length_options(delivery_type: str | None) -> list[str] | None:
    if _normalize_text(delivery_type) in {_normalize_text(x) for x in SKIP_LENGTH_FOR}:
        return None
    return list(LENGTHS)


def get_delivery_options() -> list[str]:
    return list(DELIVERY_TYPES)


def get_line_options() -> list[str]:
    return list(LINES)


def get_available_foot_movements() -> list[str]:
    return list(FOOT_MOVEMENTS)


def get_available_stroke_types() -> list[str]:
    return list(STROKE_TYPES)


def get_available_stroke_intents() -> list[str]:
    return list(STROKE_INTENTS)


def get_available_shots(foot_movement: str, stroke_type: str, bowler_type: str) -> list[str]:
    foot = next((k for k in SHOT_LIBRARY if _normalize_text(k) == _normalize_text(foot_movement)), None)
    stype = _canonical_stroke_type(stroke_type)
    btype = "Spin" if _normalize_text(bowler_type) == "spin" else "Pace"
    if not foot:
        return []
    return list(SHOT_LIBRARY.get(foot, {}).get(stype, {}).get(btype, []))


# ---------------------------------------------------------------------
# Match-quality inference
# ---------------------------------------------------------------------

def _shot_family_from_name(specific_shot: str | None) -> str | None:
    text = _normalize_text(specific_shot)
    if not text:
        return None

    for key, family in SHOT_KEYWORDS.items():
        if key in text:
            return family
    return None


def infer_match_quality(
    delivery_type: str | None,
    line: str | None,
    length: str | None,
    foot_movement: str | None,
    stroke_type: str | None,
    specific_shot: str | None,
    bowler_is_pace_or_spin: str | None,
) -> str:
    """
    Heuristic quality model from blueprint:
    Excellent / Good / Neutral / Poor / Terrible
    """

    d = _normalize_text(delivery_type)
    l = _normalize_text(line)
    ln = _normalize_text(length)
    foot = _normalize_text(foot_movement)
    stype = _canonical_stroke_type(stroke_type)
    bowler_type = _canonical_bowler_type(bowler_is_pace_or_spin, delivery_type)
    shot_family = _shot_family_from_name(specific_shot)

    score = 0

    # Delivery family effects
    if d in {"yorker ball", "yorker"}:
        score -= 3
        if foot == "advance":
            score -= 3
        if foot == "back foot":
            score -= 3
        if stype == "Lofted Shot":
            score -= 2
        if shot_family in {"defence", "push"} and foot == "front foot":
            score += 1
        if shot_family in {"pull", "hook", "upper_cut", "cut"}:
            score -= 5

    elif d in {"bouncer ball", "bouncer"}:
        score -= 1
        if foot == "back foot":
            score += 2
        if shot_family in {"pull", "hook", "upper_cut"}:
            score += 2
        if foot == "advance":
            score -= 4
        if foot == "front foot" and shot_family in {"drive", "flick"}:
            score -= 4
        if foot != "back foot" and shot_family in {"pull", "hook", "upper_cut"}:
            score -= 3

    elif d in {"slower ball", "slow ball", "slow"}:
        score += 0
        if shot_family in {"slog", "loft"}:
            score += 1
        if shot_family in {"defence"}:
            score += 1

    elif d in {"fast ball", "fast"}:
        score += 0
        if stype == "Ground Shot" and foot in {"front foot", "back foot"}:
            score += 1

    # Length effects
    back_foot_only_families = {"pull", "hook", "upper_cut", "cut"}

    if ln in {"short length", "short"}:
        if foot == "back foot":
            score += 2
        if shot_family in {"cut", "pull", "hook", "upper_cut"}:
            score += 2
        if foot == "advance":
            score -= 4
        if foot == "front foot" and shot_family in {"drive", "flick"}:
            score -= 3
        if foot != "back foot" and shot_family in back_foot_only_families:
            score -= 3

    elif ln in {"good length", "good"}:
        if foot == "front foot":
            score += 2
        if shot_family in {"drive", "defence", "push"}:
            score += 1
        if foot == "advance":
            score -= 1
        if shot_family in back_foot_only_families and foot != "back foot":
            score -= 2

    elif ln in {"full length", "full"}:
        if foot == "front foot":
            score += 1
        if shot_family in {"drive", "flick"}:
            score += 2
        if foot == "back foot" and shot_family in back_foot_only_families:
            score -= 4
        if shot_family in back_foot_only_families:
            score -= 3

    elif ln in {"yorker length", "yorker"}:
        score -= 2
        if shot_family in {"defence", "push"} and foot == "front foot":
            score += 1
        if foot == "advance":
            score -= 3
        if foot == "back foot":
            score -= 3
        if shot_family in back_foot_only_families:
            score -= 5
        if stype == "Lofted Shot":
            score -= 2

    # Line effects
    if l in {"wide of off stump", "off stump", "wide off stump"}:
        if shot_family in {"drive", "cut"}:
            score += 2
        if shot_family in {"flick", "pull"}:
            score -= 1

    elif l in {"middle stump"}:
        if shot_family in {"drive", "defence", "push"}:
            score += 1
        if shot_family in {"sweep", "reverse sweep"}:
            score += 0

    elif l in {"leg stump"}:
        if shot_family in {"flick", "pull", "hook", "sweep"}:
            score += 2
        if shot_family in {"drive", "cut"}:
            score -= 1

    # Bowler type match-ups
    if bowler_type == "Spin":
        if shot_family in {"sweep", "drive", "flick"}:
            score += 1
        if shot_family in {"pull", "hook"}:
            score -= 1
    else:  # Pace
        if shot_family in {"pull", "hook", "cut", "drive"}:
            score += 1
        if shot_family in {"sweep", "reverse sweep"}:
            score -= 1

    if score >= 5:
        return "excellent"
    if score >= 2:
        return "good"
    if score >= -1:
        return "neutral"
    if score >= -4:
        return "poor"
    return "terrible"


# ---------------------------------------------------------------------
# Probability application
# ---------------------------------------------------------------------

def _apply_delta(probs: dict[str, float], deltas: dict[str, float]) -> dict[str, float]:
    result = dict(probs)
    for key, delta in deltas.items():
        if key == "boundary_mult":
            continue
        result[key] = result.get(key, 0.0) + delta
    return result


def _normalize(probs: dict[str, float]) -> dict[str, float]:
    probs = {k: max(0.0, float(v)) for k, v in probs.items()}
    total = sum(probs.values())
    if total <= 0:
        return {k: (1.0 if k == "dot" else 0.0) for k in OUTCOMES}
    return {k: v / total for k, v in probs.items()}


def _apply_random_variation(probs: dict[str, float], spread: float = 0.015) -> dict[str, float]:
    """
    Adds tiny variation so the bot doesn't feel mechanically repetitive.
    """
    adjusted = dict(probs)
    for key in adjusted:
        if key == "mishit":
            continue
        jitter = random.uniform(-spread, spread)
        adjusted[key] = max(0.0, adjusted[key] + jitter)
    return _normalize(adjusted)


def calculate_outcome_probabilities(
    bowler_level: int,
    batsman_level: int,
    balls_faced: int,
    stroke_type: str,
    stroke_intent: str,
    shot_match_quality: str = "neutral",
    *,
    delivery_type: str | None = None,
    line: str | None = None,
    length: str | None = None,
    foot_movement: str | None = None,
    specific_shot: str | None = None,
    bowler_is_pace_or_spin: str | None = None,
    batsman_experience: str | None = None,
    random_variation: bool = False,
) -> dict[str, float]:
    """
    Returns a normalized probability dictionary for all OUTCOMES.

    Backward-compatible positional args:
        bowler_level, batsman_level, balls_faced, stroke_type, stroke_intent, shot_match_quality

    Extended blueprint args:
        delivery_type, line, length, foot_movement, specific_shot,
        bowler_is_pace_or_spin, batsman_experience
    """
    context = BallContext(
        bowler_level=bowler_level,
        batsman_level=batsman_level,
        balls_faced=balls_faced,
        delivery_type=delivery_type,
        line=line,
        length=length,
        foot_movement=foot_movement,
        stroke_type=stroke_type,
        stroke_intent=stroke_intent,
        specific_shot=specific_shot,
        bowler_is_pace_or_spin=bowler_is_pace_or_spin,
        batsman_experience=batsman_experience,
        shot_match_quality=shot_match_quality,
    )

    profile_probs = _profile_probability_weights(context)
    if profile_probs is not None:
        if random_variation:
            profile_probs = _apply_random_variation(profile_probs)
        return profile_probs

    canonical_stroke_type = _canonical_stroke_type(stroke_type)
    canonical_intent = _canonical_stroke_intent(stroke_intent)

    base = dict(BASE_PROBS[(canonical_stroke_type, canonical_intent)])

    tier = experience_tier(balls_faced, batsman_experience)
    probs = _apply_delta(base, EXPERIENCE_MODIFIERS[tier])
    probs = _apply_delta(probs, INTENT_MODIFIERS[canonical_intent])
    probs = _apply_delta(probs, skill_diff_modifier(bowler_level, batsman_level))

    # If the caller provided richer context, infer match quality from the context.
    inferred_quality = shot_match_quality
    if any(v is not None for v in [delivery_type, line, length, foot_movement, specific_shot, bowler_is_pace_or_spin]):
        inferred_quality = infer_match_quality(
            delivery_type=delivery_type,
            line=line,
            length=length,
            foot_movement=foot_movement,
            stroke_type=canonical_stroke_type,
            specific_shot=specific_shot,
            bowler_is_pace_or_spin=bowler_is_pace_or_spin,
        )

    match_mod = shot_match_modifier(inferred_quality)
    probs["boundary"] = probs.get("boundary", 0.0) * match_mod.get("boundary_mult", 1.0)
    probs["six"] = probs.get("six", 0.0) * match_mod.get("six_mult", 1.0)
    if inferred_quality in {"poor", "terrible"}:
        original_six = base.get("six", 0.0)
        suppressed_amount = max(0.0, original_six - probs.get("six", 0.0))
        probs["mishit"] = probs.get("mishit", 0.0) + suppressed_amount * 0.4
        probs["dot"] = probs.get("dot", 0.0) + suppressed_amount * 0.2
    probs = _apply_delta(probs, {"out": match_mod.get("out", 0.0)})

    # Specific-shot profile to reflect the named batting shot.
    family = _shot_family_from_name(specific_shot)
    if family and family in SHOT_PROFILE_MODIFIERS:
        probs = _apply_delta(probs, SHOT_PROFILE_MODIFIERS[family])

    # Clamp and normalize
    probs = _normalize(probs)

    hard_caps = {
        "terrible": {"six": 0.03, "boundary": 0.10},
        "poor": {"six": 0.08, "boundary": 0.20},
    }
    if inferred_quality in hard_caps:
        caps = hard_caps[inferred_quality]
        for key, cap in caps.items():
            if probs.get(key, 0.0) > cap:
                excess = probs[key] - cap
                probs[key] = cap
                probs["out"] = probs.get("out", 0.0) + excess * 0.6
                probs["mishit"] = probs.get("mishit", 0.0) + excess * 0.4
        probs = _normalize(probs)

    # Optional tiny noise for live feel
    if random_variation:
        probs = _apply_random_variation(probs)

    return probs


def roll_outcome(probabilities: dict[str, float]) -> str:
    outcomes = list(probabilities.keys())
    weights = list(probabilities.values())
    return random.choices(outcomes, weights=weights, k=1)[0]


def resolve_ball(
    bowler_level: int,
    batsman_level: int,
    balls_faced: int,
    stroke_type: str,
    stroke_intent: str,
    shot_match_quality: str = "neutral",
    *,
    delivery_type: str | None = None,
    line: str | None = None,
    length: str | None = None,
    foot_movement: str | None = None,
    specific_shot: str | None = None,
    bowler_is_pace_or_spin: str | None = None,
    batsman_experience: str | None = None,
    random_variation: bool = False,
) -> str:
    """
    Convenience wrapper for the match engine.
    """
    probs = calculate_outcome_probabilities(
        bowler_level=bowler_level,
        batsman_level=batsman_level,
        balls_faced=balls_faced,
        stroke_type=stroke_type,
        stroke_intent=stroke_intent,
        shot_match_quality=shot_match_quality,
        delivery_type=delivery_type,
        line=line,
        length=length,
        foot_movement=foot_movement,
        specific_shot=specific_shot,
        bowler_is_pace_or_spin=bowler_is_pace_or_spin,
        batsman_experience=batsman_experience,
        random_variation=random_variation,
    )
    return roll_outcome(probs)


def ball_result_from_context(context: BallContext) -> dict[str, Any]:
    """
    Returns a richer result object that downstream score/commentary engines
    can consume later.
    """
    outcome = resolve_ball(
        bowler_level=context.bowler_level,
        batsman_level=context.batsman_level,
        balls_faced=context.balls_faced,
        stroke_type=context.stroke_type or "Ground Shot",
        stroke_intent=context.stroke_intent or "Neutral",
        shot_match_quality=context.shot_match_quality or "neutral",
        delivery_type=context.delivery_type,
        line=context.line,
        length=context.length,
        foot_movement=context.foot_movement,
        specific_shot=context.specific_shot,
        bowler_is_pace_or_spin=context.bowler_is_pace_or_spin,
        batsman_experience=context.batsman_experience,
    )

    runs_map = {
        "dot": 0,
        "single": 1,
        "double": 2,
        "triple": 3,
        "four": 4,
        "five": 5,
        "boundary": 4,
        "six": 6,
        "wide": 1,
        "no_ball": 1,
        "leg_bye": 1,
        "bye": 1,
        "out": 0,
        "run_out": 0,
        "mishit": 0,
    }

    return {
        "outcome": outcome,
        "runs": runs_map.get(outcome, 0),
        "wicket": outcome in {"out", "run_out"},
        "extra_type": None,
        "bowler_level": context.bowler_level,
        "batsman_level": context.batsman_level,
        "balls_faced": context.balls_faced,
        "stroke_type": context.stroke_type,
        "stroke_intent": context.stroke_intent,
        "specific_shot": context.specific_shot,
        "delivery_type": context.delivery_type,
        "line": context.line,
        "length": context.length,
        "foot_movement": context.foot_movement,
        "bowler_is_pace_or_spin": context.bowler_is_pace_or_spin,
        "batsman_experience": context.batsman_experience,
    }


# ---------------------------------------------------------------------
# Small validation helpers for future engines
# ---------------------------------------------------------------------

def validate_delivery_type(delivery_type: str | None) -> bool:
    return _normalize_text(delivery_type) in {_normalize_text(x) for x in DELIVERY_TYPES}


def validate_line(line: str | None) -> bool:
    return _normalize_text(line) in {_normalize_text(x) for x in LINES}


def validate_length(length: str | None) -> bool:
    return _normalize_text(length) in {_normalize_text(x) for x in LENGTHS}


def validate_foot_movement(foot_movement: str | None) -> bool:
    return _normalize_text(foot_movement) in {_normalize_text(x) for x in FOOT_MOVEMENTS}


def validate_stroke_type(stroke_type: str | None) -> bool:
    return _canonical_stroke_type(stroke_type) in STROKE_TYPES


def validate_intent(stroke_intent: str | None) -> bool:
    return _canonical_stroke_intent(stroke_intent) in STROKE_INTENTS


def describe_probability_table(probabilities: dict[str, float]) -> str:
    """
    Human-friendly debug string. Useful while tuning the engine.
    """
    parts = []
    for key in OUTCOMES:
        if key in probabilities:
            parts.append(f"{key}={probabilities[key]:.3f}")
    return ", ".join(parts)
