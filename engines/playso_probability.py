"""Dedicated PLAYSO probability model.

Independent from engines/tactics_engine.py. The model follows the supplied
PLAYSO blueprint: pitch, delivery/line/length, foot/intent/shot and bounded
batter-vs-bowler level pressure. Every distribution normalizes to 100%.
"""
from __future__ import annotations

import random
from typing import Dict

PITCHES = {"green", "dry", "dusty", "flat", "hard", "even", "bouncy", "slow"}

BASE = {
    ("back", "back", "grounded"): [24, 40, 10, 1, 18, 3, 4],
    ("back", "front", "grounded"): [30, 38, 9, 1, 15, 1, 6],
    ("short", "back", "grounded"): [22, 39, 10, 1, 19, 4, 5],
    ("short", "front", "grounded"): [38, 34, 7, .5, 13, 1.5, 6],
    ("full", "front", "grounded"): [22, 36, 10, 1, 23, 3, 5],
    ("full", "back", "grounded"): [34, 39, 8, .5, 14, .5, 4],
    ("back", "back", "lofted"): [18, 24, 8, 1, 25, 17, 7],
    ("back", "front", "lofted"): [28, 24, 6, .5, 16, 15, 10.5],
    ("short", "back", "lofted"): [16, 22, 8, 1, 25, 20, 8],
    ("short", "front", "lofted"): [35, 22, 6, .5, 16, 9, 11.5],
    ("full", "front", "lofted"): [14, 24, 8, 1, 26, 18, 9],
    ("full", "back", "lofted"): [31, 24, 7, .5, 18, 10, 9.5],
}

DELIVERIES = {
    "pace": {
        "back": ["Back-of-Length Slower Ball", "Hard-Length Ball", "Seam-Up Back of Length", "Cross-Seam Back of Length", "Back-of-Length Cutter", "Back-of-Length Angled Ball"],
        "full": ["Full-Length Slower Ball", "Full Seam Ball", "Full Inswinger", "Full Outswinger", "Full Cutter", "Full Straight Ball"],
        "short": ["Short Slower Ball", "Short Ball", "Bouncer", "Chest-High Bouncer", "Short Cutter", "Rising Short Ball"],
    },
    "offspin": {
        "back": ["Off Break", "Arm Ball", "Topspinner", "Doosra", "Flighted", "Faster Ball"],
        "full": ["Full Off Break", "Full Arm Ball", "Full Topspinner", "Full Doosra", "Full Flighted", "Full Faster Ball"],
    },
    "legspin": {
        "back": ["Leg-Break", "Googly", "Topspinner", "Slider", "Flipper", "Flighted"],
        "full": ["Full Leg-Break", "Full Googly", "Full Topspinner", "Full Slider", "Full Flipper", "Full Flighted"],
    },
}


GROUNDED_SHOTS = {
    "back": ["Backfoot Defence", "Backfoot Punch", "Square Cut", "Late Cut", "Pull", "Ramp"],
    "front": ["Defence", "Cover Drive", "Straight Drive", "Square Drive", "Flick", "Push"],
    "advance": ["Charge Defence", "Charge Push", "Charge Cover Drive", "Charge Straight Drive", "Charge Flick", "Charge Cut"],
}
LOFTED_SHOTS = {
    "back": ["Lofted Pull", "Hook", "Upper Cut", "Lofted Cut", "Slog", "Ramp/Scoop"],
    "front": ["Lofted Straight Drive", "Lofted Cover Drive", "Inside-Out", "Lofted Flick", "Slog", "Scoop"],
    "advance": ["Charge Loft Straight", "Charge Loft Cover", "Inside-Out Loft", "Slog", "Scoop", "Switch Hit"],
}

OUTCOMES = ["0", "1", "2", "3", "4", "5", "6", "WIDE", "NO_BALL", "LEG_BYE", "BYE", "OUT"]


def deliveries_for_family(family: str, length: str = "full") -> list[str]:
    family_map = DELIVERIES.get(family, DELIVERIES["pace"])
    return list(family_map.get(length, next(iter(family_map.values()))))


def shots_for(foot: str, intent: str) -> list[str]:
    return list((GROUNDED_SHOTS if intent == "grounded" else LOFTED_SHOTS).get(foot, GROUNDED_SHOTS["front"]))


def _renorm(vals: Dict[str, float]) -> Dict[str, float]:
    total = sum(max(0.0, v) for v in vals.values()) or 1.0
    out = {k: max(0.0, v) * 100.0 / total for k, v in vals.items()}
    # force exact 100.00 within floating point display without distorting choices
    last = next(reversed(out))
    out[last] += 100.0 - sum(out.values())
    return out


def probability_distribution(*, pitch: str, family: str, delivery: str, line: str,
                              length: str, foot: str, intent: str, shot: str,
                              batter_level: int, bowler_level: int) -> Dict[str, float]:
    pitch = pitch if pitch in PITCHES else "even"
    key = (length, foot, intent)
    base = list(BASE.get(key, BASE[("full", "front", "grounded")]))
    vals = {k: float(v) for k, v in zip(["0", "1", "2", "3", "4", "6", "OUT"], base)}

    # Match quality: deliberate but bounded; strong human matchup remains the main lever.
    shot_low = shot.lower()
    match = 0.0
    if length == "full" and foot in {"front", "advance"}:
        match += 4
    if length == "back" and foot == "back":
        match += 3
    if length == "short" and foot == "back":
        match += 4
    if length == "short" and foot == "front":
        match -= 4
    if length == "full" and foot == "back":
        match -= 3
    if "drive" in shot_low and length == "full":
        match += 2
    if any(w in shot_low for w in ["cut", "pull", "hook", "upper cut"]) and length == "short":
        match += 3
    if family in {"offspin", "legspin"} and any(w in shot_low for w in ["sweep", "scoop", "switch"]):
        match += 2
    if family == "pace" and delivery in {"Bouncer", "Hard Length"} and foot == "back":
        match += 1
    if delivery in {"Yorker", "Bouncer"} and foot == "front":
        match -= 2

    # Pitch effect is modest and never zeroes out an outcome.
    if pitch in {"green", "bouncy"} and family == "pace":
        match += 2
        vals["OUT"] += 0.8
    elif pitch in {"dry", "dusty", "slow"} and family in {"offspin", "legspin"}:
        match += 2
        vals["OUT"] += 0.6
    elif pitch == "flat":
        vals["4"] += 1.5
        vals["6"] += 1.0
        vals["OUT"] -= 0.5

    # Level-vs-level: dedicated PLAYSO layer, bounded and independent from tactics_engine.
    level_edge = max(-18.0, min(18.0, (int(batter_level) - int(bowler_level)) * 0.35))
    match += level_edge * 0.15

    # Match quality shifts boundary mass while keeping rare 3/5 and extras alive.
    if match > 0:
        vals["4"] *= 1.0 + min(0.18, match * 0.018)
        vals["6"] *= 1.0 + min(0.20, match * 0.020)
        vals["OUT"] *= max(0.82, 1.0 - match * 0.012)
    else:
        penalty = min(0.45, abs(match) * 0.025)
        vals["4"] *= 1.0 - penalty
        vals["6"] *= 1.0 - penalty * 1.15
        vals["OUT"] *= 1.0 + penalty * 0.45
        vals["0"] *= 1.0 + penalty * 0.55

    # Wide/No-ball/Bye/Leg-bye are separate low-mass possibilities.
    extras = {"WIDE": 1.0, "NO_BALL": 0.65, "LEG_BYE": 0.45, "BYE": 0.35}
    if line in {"wide_off", "leg"}:
        extras["WIDE"] += 0.55
    if family == "pace" and delivery in {"Bouncer", "Yorker"}:
        extras["NO_BALL"] *= 0.92
    if intent == "lofted":
        extras["WIDE"] *= 1.04

    main_total = sum(vals.values())
    extra_mass = sum(extras.values())
    # Reserve at most 4% for extras, distributed according to line/delivery.
    reserved = min(4.0, extra_mass)
    factor = reserved / extra_mass if extra_mass else 0
    dist = {k: v * (100.0 - reserved) / main_total for k, v in vals.items()}
    dist.update({k: v * factor for k, v in extras.items()})
    return _renorm(dist)


def sample_outcome(dist: Dict[str, float]) -> str:
    roll = random.random() * 100.0
    cumulative = 0.0
    for outcome in OUTCOMES:
        cumulative += dist.get(outcome, 0.0)
        if roll <= cumulative:
            return outcome
    return "0"
