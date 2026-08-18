"""
Full tactical ball-outcome engine for /play.

Layered, in order, each layer multiplying the running weights from
the layer before it:

  1. BASE_WEIGHTS_BY_MINDSET - each batting mindset's own outcome
     shape (defensive is NOT just "high dot%", it has its own rare
     streaky-boundary floor; rotate is genuinely 1s/2s-dominant, not
     just a softer defensive; etc).
  2. TACTICAL_MODIFIERS - bowler tactic x batter mindset interaction
     (25 combos). Two design rules baked into these tables:
       - "Mirrored intensity": when a containing bowling tactic meets
         a matching containing batting mindset (defensive-vs-defensive,
         back-of-length-vs-defensive), the wicket chance is standard
         or slightly elevated, NOT suppressed - two cagey sides
         produce false shots and lapses, not automatic safety.
       - "Even-number bias": back-of-length and variation against
         neutral/aggressive mindsets suppress the easy 1s/3s (denying
         free rotation) while still allowing genuine boundaries
         through, rather than just suppressing everything equally.
  3. PITCH_MATRIX - the 8 pitch conditions (unchanged shape from the
     original design, still sits on top of everything else).
  4. PHASE_MATRIX - powerplay / middle / death overs.
  5. LEVEL_DIFF_MATRIX - bowler level vs batter level (both 35-100).
     Whoever's level is ahead gets the matchup advantage.
  6. CONFIDENCE_ZONE_MATRIX - the batter's current confidence zone
     (nervous/building/set/in_the_zone). This is where the "high
     confidence isn't a blanket shield" rule lives: a batter In The
     Zone (80-100%) facing a bowler on the Variation tactic while
     playing Aggressive/Ultra Aggressive gets that protection
     overridden back to standard - Variation is specifically the
     tactic that should still trouble a batter who's committing hard
     regardless of how set they are.

engines/play_runtime.py owns the batter's confidence VALUE and the
consecutive-boundary streak (that's per-batter state, tracked on
BatterSlot) - this module is pure lookup/math, no state of its own.
"""

from __future__ import annotations

import random

OUTCOMES = [0, 1, 2, 3, 4, 6, "W", "WD", "NB", "LB", "BY"]

BOWLER_TACTICS = [
    "defensive", "swinging", "pace_up", "back_of_length", "variation",
    # Off-spin family
    "off_break", "doosra", "arm_ball", "carrom_ball", "top_spin",
    # Leg-spin family
    "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball",
]
BATTER_MINDSETS = ["defensive", "rotate", "neutral", "aggressive", "ultra_aggressive"]

# --- LAYER 1: BASE WEIGHTS PER BATTING MINDSET ---
# Each mindset gets its own shape rather than one shared baseline -
# defensive and rotate look meaningfully different from each other
# (dot-heavy vs 1s/2s-heavy), and both keep a small non-zero boundary
# floor (streaky edges, misfields) instead of a hard boundary lockout.
BASE_WEIGHTS_BY_MINDSET: dict = {
    "defensive": {0: 48.0, 1: 26.0, 2: 6.0, 3: 0.8, 4: 3.5, 6: 0.3, "W": 3.0, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "rotate": {0: 22.0, 1: 40.0, 2: 14.0, 3: 1.5, 4: 4.5, 6: 0.5, "W": 3.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "neutral": {0: 28.0, 1: 32.0, 2: 14.0, 3: 1.2, 4: 9.0, 6: 2.5, "W": 4.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "aggressive": {0: 22.0, 1: 24.0, 2: 9.0, 3: 0.8, 4: 15.0, 6: 8.0, "W": 7.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "ultra_aggressive": {0: 14.0, 1: 16.0, 2: 5.0, 3: 0.4, 4: 19.0, 6: 17.0, "W": 12.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
}

# Spinner buttons are deliberate delivery names, but they use the same
# five tactical probability shapes as the original bowling tactics.  The
# mapping keeps the existing probability behavior while letting the UI
# reflect the actual bowler type and selected spinner delivery.
SPINNER_TACTIC_ARCHETYPE = {
    "off_break": "defensive",
    "doosra": "variation",
    "arm_ball": "swinging",
    "carrom_ball": "variation",
    "top_spin": "back_of_length",
    "leg_breaker": "swinging",
    "top_spinner": "back_of_length",
    "slider": "variation",
    "flipper": "pace_up",
    "googly_ball": "variation",
}

FAST_BOWLER_STYLES = {"RAF", "LAF", "RAM", "LAM"}
OFF_SPIN_STYLES = {"RAO", "LAO"}
LEG_SPIN_STYLES = {"RAL", "LAL"}


def bowler_family(bowler_style: str | None, bowler_role: str | None = None) -> str:
    text = str(bowler_style or bowler_role or "").strip().upper().replace(" ", "")
    if text in OFF_SPIN_STYLES or "OFFBREAK" in text or "OFFSPIN" in text:
        return "spin"
    if text in LEG_SPIN_STYLES or "LEGSPIN" in text or "LEG-BREAK" in text:
        return "spin"
    if text in FAST_BOWLER_STYLES or any(k in text for k in ("FAST", "MEDIUM", "SEAM", "PACE")):
        return "pace"
    if "SPIN" in text or "ORTHODOX" in text or "CHINAMAN" in text:
        return "spin"
    return "pace"


def _tactic_archetype(tactic: str) -> str:
    return SPINNER_TACTIC_ARCHETYPE.get(tactic, tactic)

# Pairs where bowler tactic and batter mindset are the "same energy" -
# both playing it cagey/measured. Wicket weight for these specific
# pairs is set to standard-or-slightly-elevated in the tables below,
# never suppressed the way a containing tactic normally suppresses a
# mismatched, more attacking mindset.
MIRRORED_PAIRS = {("defensive", "defensive"), ("back_of_length", "defensive")}

# --- LAYER 2: TACTICAL MODIFIER MAP (bowler_tactic, batter_mindset) ---
TACTICAL_MODIFIERS: dict = {
    ("defensive", "defensive"): {0: 1.30, 1: 0.85, 2: 0.80, 3: 0.60, 4: 0.50, 6: 0.30, "W": 1.10, "WD": 0.60, "NB": 0.60, "LB": 0.90, "BY": 0.90},
    ("defensive", "rotate"): {0: 0.70, 1: 1.50, 2: 1.30, 3: 1.10, 4: 0.50, 6: 0.30, "W": 0.75, "WD": 0.70, "NB": 0.60, "LB": 1.00, "BY": 1.00},
    ("defensive", "neutral"): {0: 1.20, 1: 1.00, 2: 0.95, 3: 0.80, 4: 0.65, 6: 0.50, "W": 0.85, "WD": 0.80, "NB": 0.70, "LB": 1.00, "BY": 1.00},
    ("defensive", "aggressive"): {0: 1.00, 1: 0.75, 2: 0.85, 3: 0.55, 4: 1.20, 6: 1.10, "W": 1.15, "WD": 0.80, "NB": 0.80, "LB": 0.90, "BY": 0.90},
    ("defensive", "ultra_aggressive"): {0: 0.65, 1: 0.45, 2: 0.55, 3: 0.35, 4: 1.60, 6: 1.90, "W": 1.55, "WD": 0.70, "NB": 0.70, "LB": 0.80, "BY": 0.80},

    ("swinging", "defensive"): {0: 1.70, 1: 0.55, 2: 0.45, 3: 0.25, 4: 0.15, 6: 0.05, "W": 1.25, "WD": 1.40, "NB": 1.10, "LB": 1.50, "BY": 1.20},
    ("swinging", "rotate"): {0: 1.05, 1: 1.15, 2: 1.05, 3: 0.95, 4: 0.60, 6: 0.40, "W": 1.15, "WD": 1.30, "NB": 1.10, "LB": 1.30, "BY": 1.10},
    ("swinging", "neutral"): {0: 1.05, 1: 0.90, 2: 0.85, 3: 0.80, 4: 1.00, 6: 0.90, "W": 1.40, "WD": 1.30, "NB": 1.10, "LB": 1.20, "BY": 1.10},
    ("swinging", "aggressive"): {0: 0.70, 1: 0.70, 2: 0.80, 3: 0.90, 4: 1.55, 6: 1.45, "W": 1.85, "WD": 1.20, "NB": 1.10, "LB": 1.10, "BY": 1.10},
    ("swinging", "ultra_aggressive"): {0: 0.40, 1: 0.40, 2: 0.50, 3: 0.60, 4: 2.20, 6: 2.40, "W": 2.55, "WD": 1.20, "NB": 1.10, "LB": 1.00, "BY": 1.00},

    ("pace_up", "defensive"): {0: 1.90, 1: 0.40, 2: 0.30, 3: 0.20, 4: 0.05, 6: 0.05, "W": 1.15, "WD": 1.20, "NB": 1.30, "LB": 1.40, "BY": 1.50},
    ("pace_up", "rotate"): {0: 0.85, 1: 1.25, 2: 1.15, 3: 1.05, 4: 0.80, 6: 0.50, "W": 1.05, "WD": 1.10, "NB": 1.20, "LB": 1.20, "BY": 1.30},
    ("pace_up", "neutral"): {0: 0.95, 1: 1.00, 2: 1.00, 3: 1.00, 4: 1.20, 6: 1.10, "W": 1.35, "WD": 1.10, "NB": 1.20, "LB": 1.10, "BY": 1.20},
    ("pace_up", "aggressive"): {0: 0.60, 1: 0.80, 2: 0.90, 3: 1.00, 4: 1.75, 6: 1.65, "W": 1.65, "WD": 1.00, "NB": 1.20, "LB": 1.00, "BY": 1.10},
    ("pace_up", "ultra_aggressive"): {0: 0.30, 1: 0.50, 2: 0.60, 3: 0.80, 4: 2.30, 6: 2.50, "W": 2.25, "WD": 1.00, "NB": 1.30, "LB": 0.90, "BY": 1.00},

    ("back_of_length", "defensive"): {0: 2.00, 1: 0.50, 2: 0.50, 3: 0.30, 4: 0.05, 6: 0.02, "W": 1.10, "WD": 1.00, "NB": 1.00, "LB": 1.10, "BY": 1.00},
    ("back_of_length", "rotate"): {0: 1.20, 1: 0.90, 2: 1.15, 3: 0.75, 4: 0.40, 6: 0.25, "W": 0.85, "WD": 1.00, "NB": 1.00, "LB": 1.00, "BY": 1.00},
    ("back_of_length", "neutral"): {0: 1.30, 1: 0.75, 2: 1.15, 3: 0.65, 4: 0.75, 6: 0.60, "W": 1.00, "WD": 1.00, "NB": 1.00, "LB": 1.00, "BY": 1.00},
    ("back_of_length", "aggressive"): {0: 1.10, 1: 0.55, 2: 1.10, 3: 0.60, 4: 1.20, 6: 0.85, "W": 1.30, "WD": 1.00, "NB": 1.00, "LB": 0.90, "BY": 0.90},
    ("back_of_length", "ultra_aggressive"): {0: 0.60, 1: 0.45, 2: 0.70, 3: 0.50, 4: 1.70, 6: 1.50, "W": 1.75, "WD": 0.90, "NB": 1.00, "LB": 0.90, "BY": 0.90},

    ("variation", "defensive"): {0: 1.75, 1: 0.40, 2: 0.50, 3: 0.15, 4: 0.10, 6: 0.03, "W": 0.90, "WD": 1.50, "NB": 1.10, "LB": 1.20, "BY": 1.30},
    ("variation", "rotate"): {0: 0.85, 1: 1.30, 2: 1.30, 3: 0.85, 4: 0.50, 6: 0.30, "W": 1.15, "WD": 1.40, "NB": 1.10, "LB": 1.10, "BY": 1.20},
    ("variation", "neutral"): {0: 1.00, 1: 0.85, 2: 1.15, 3: 0.75, 4: 1.05, 6: 0.85, "W": 1.40, "WD": 1.40, "NB": 1.10, "LB": 1.00, "BY": 1.10},
    ("variation", "aggressive"): {0: 0.70, 1: 0.65, 2: 1.00, 3: 0.65, 4: 1.55, 6: 1.35, "W": 1.75, "WD": 1.30, "NB": 1.10, "LB": 0.90, "BY": 1.00},
    ("variation", "ultra_aggressive"): {0: 0.45, 1: 0.40, 2: 0.60, 3: 0.50, 4: 2.10, 6: 2.20, "W": 2.40, "WD": 1.30, "NB": 1.10, "LB": 0.80, "BY": 0.90},
}

# --- LAYER 3: ENVIRONMENTAL (PITCH CONDITION MAP) ---
PITCH_MATRIX: dict = {
    "green": {"W": 1.48, 0: 1.28, 4: 0.76, 6: 0.62, 1: 0.96, "WD": 1.10},
    "dusty": {"W": 1.42, 0: 1.24, 1: 1.10, 4: 0.74, 6: 0.62, "BY": 1.30},
    "dry": {1: 1.10, 2: 1.06, 4: 0.78, 6: 0.70, "W": 1.28, 0: 1.08},
    "hard": {4: 1.20, 6: 1.15, 0: 0.90, "W": 1.10, "NB": 1.20},
    "flat": {4: 1.40, 6: 1.45, 0: 0.70, "W": 0.60, "WD": 0.90},
    "bouncy": {6: 1.05, "W": 1.34, 0: 1.18, 1: 0.90, 4: 0.82, "LB": 1.20},
    "slow": {0: 1.22, 1: 1.08, 4: 0.72, 6: 0.66, "W": 1.24},
    "even": {0: 1.00, 1: 1.00, 4: 1.00, 6: 1.00, "W": 1.00},
}

def _pitch_match_for_bowler(pitch: str, bowler_style: str | None, bowler_role: str | None) -> bool:
    family = bowler_family(bowler_style, bowler_role)
    pitch = str(pitch or "").strip().lower()
    return (pitch in {"green", "bouncy"} and family == "pace") or (
        pitch in {"dry", "dusty", "slow"} and family == "spin"
    )


def _apply_pitch_edge(weights: dict, pitch: str, batsman_balls_faced: int, bowler_style: str | None, bowler_role: str | None) -> bool:
    """Apply the additional bowling-pitch pressure only when the actual
    bowler family matches the pitch.  The early-confidence effect is a
    controlled risk increase, not a blanket attacking-intent rewrite.
    Returns whether the pitch/bowler pairing is a true match."""
    if not _pitch_match_for_bowler(pitch, bowler_style, bowler_role):
        return False

    balls = max(0, int(batsman_balls_faced or 0))
    if balls <= 5:
        _apply(weights, {
            0: 1.10, 1: 1.02, 2: 0.98, 3: 0.92,
            4: 0.84, 6: 0.76, "W": 1.12,
        })
    elif balls <= 15:
        _apply(weights, {
            0: 1.06, 1: 1.04, 2: 1.02, 3: 0.98,
            4: 0.92, 6: 0.84, "W": 1.04,
        })
    elif balls <= 30:
        _apply(weights, {0: 1.01, 4: 0.97, 6: 0.94, "W": 1.02})
    elif balls <= 45:
        _apply(weights, {0: 1.00, 4: 0.99, 6: 0.97, "W": 1.01})
    else:
        _apply(weights, {0: 1.00, 4: 0.995, 6: 0.985, "W": 1.005})
    return True


def _cap_early_pitch_wicket_risk(weights: dict, batsman_balls_faced: int) -> None:
    """Keep wicket clusters realistic on matched bowling-friendly pitches.
    The cap is deliberately permissive: two wickets in an over can happen,
    three-plus stays rare, and larger clusters remain extremely unlikely."""
    balls = max(0, int(batsman_balls_faced or 0))
    cap = 0.22 if balls <= 5 else (0.16 if balls <= 15 else (0.135 if balls <= 30 else (0.12 if balls <= 45 else 0.115)))
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0 or weights.get("W", 0.0) <= 0:
        return
    current = max(0.0, float(weights.get("W", 0.0))) / total
    if current <= cap:
        return
    non_w_total = total - max(0.0, float(weights.get("W", 0.0)))
    if non_w_total <= 0:
        return
    # Preserve the relative shape of every non-wicket outcome, only move the
    # excess wicket mass into the ordinary scoring pool.
    desired_w = cap * total
    scale_non_w = (total - desired_w) / non_w_total
    for key in list(weights):
        if key != "W":
            weights[key] = max(0.0, float(weights[key])) * scale_non_w
    weights["W"] = desired_w


# --- LAYER 4: MATCH PHASE MAP ---
PHASE_MATRIX: dict = {
    "overs_1_6": {4: 1.35, 6: 1.15, 1: 0.80, 0: 1.10, "W": 1.10},
    "overs_7_15": {1: 1.25, 2: 1.20, 4: 0.80, 6: 0.85, 0: 0.95},
    "overs_16_20": {6: 1.60, 4: 1.30, 0: 0.70, "W": 1.45, "NB": 1.30, "WD": 1.25},
}


def phase_key(over_number: int) -> str:
    if over_number <= 6:
        return "overs_1_6"
    if over_number <= 15:
        return "overs_7_15"
    return "overs_16_20"


# --- LAYER 5: BOWLER LEVEL vs BATTER LEVEL (both 35-100) ---
# Whoever's level leads the matchup gets the advantage - a big gap
# either way should be clearly felt, not a rounding error.
LEVEL_DIFF_BUCKETS = (
    (-100, -30, "bowler_dominant_large"),
    (-29, -11, "bowler_dominant"),
    (-10, -7, "bowler_dominant_gap"),
    (-6, 6, "even"),
    (7, 10, "batter_dominant_gap"),
    (11, 29, "batter_dominant"),
    (30, 100, "batter_dominant_large"),
)

LEVEL_DIFF_MATRIX: dict = {
    "bowler_dominant_large": {0: 1.22, 1: 0.92, "W": 1.45, 4: 0.68, 6: 0.58},
    "bowler_dominant": {0: 1.10, 1: 0.96, "W": 1.28, 4: 0.82, 6: 0.78},
    # 7-10 levels is a meaningful advantage, but deliberately not a free wicket.
    "bowler_dominant_gap": {0: 1.06, 1: 0.98, "W": 1.16, 4: 0.90, 6: 0.86},
    "even": {0: 1.00, 1: 1.00, "W": 1.00, 4: 1.00, 6: 1.00},
    "batter_dominant_gap": {0: 0.94, 1: 1.03, "W": 0.88, 4: 1.10, 6: 1.14},
    "batter_dominant": {0: 0.86, 1: 1.06, "W": 0.72, 4: 1.22, 6: 1.28},
    "batter_dominant_large": {0: 0.76, 1: 1.10, "W": 0.54, 4: 1.38, 6: 1.48},
}


def level_bucket(batter_level: int, bowler_level: int) -> str:
    diff = int(batter_level or 0) - int(bowler_level or 0)
    for low, high, name in LEVEL_DIFF_BUCKETS:
        if low <= diff <= high:
            return name
    return "even"


# --- LAYER 6: BATSMAN CONFIDENCE ZONE ---
# Zones (not a raw percentage) are what everything downstream reasons
# about - engines/play_runtime.py still tracks the underlying 0-100
# value (and the boundary streak used to build it up), but every
# lookup here is zone-based.
CONFIDENCE_ZONES = (
    (0, 24, "nervous"),
    (25, 39, "building"),
    (40, 79, "set"),
    (80, 100, "in_the_zone"),
)

CONFIDENCE_ZONE_MATRIX: dict = {
    "nervous": {0: 1.20, "W": 1.30, 4: 0.70, 6: 0.60},
    "building": {0: 1.05, "W": 1.10, 4: 0.90, 6: 0.85},
    "set": {0: 1.00, "W": 1.00, 4: 1.00, 6: 1.00},
    "in_the_zone": {0: 0.85, "W": 0.70, 4: 1.15, 6: 1.20},
}

# The one override on the whole engine: a batter In The Zone playing
# Aggressive/Ultra Aggressive against a bowler on Variation does NOT
# get the in_the_zone wicket discount - it goes back to standard/
# slightly elevated instead, since Variation is built to trouble
# exactly this batter, regardless of how set they are.
VARIATION_COUNTER_WICKET_MULT = 1.05


def confidence_zone(confidence: float) -> str:
    value = max(0.0, min(100.0, float(confidence or 0.0)))
    for low, high, name in CONFIDENCE_ZONES:
        if low <= value <= high:
            return name
    return "set"


def _apply(weights: dict, modifiers: dict) -> None:
    for key, mult in modifiers.items():
        weights[key] = weights.get(key, 0.0) * mult


def _dampen_level_advantage(weights: dict, batter_level: int, bowler_level: int, balls_faced: int) -> None:
    """Progressively soften level mismatch once the batter has settled.

    0-15 balls: full level effect.
    16-30: 65% of the mismatch remains.
    31-45: 40% remains.
    45+: 20% remains.
    """
    balls = max(0, int(balls_faced or 0))
    if balls <= 15:
        return
    factor = 0.65 if balls <= 30 else (0.40 if balls <= 45 else 0.20)
    diff = int(batter_level or 0) - int(bowler_level or 0)
    bucket = level_bucket(batter_level, bowler_level)
    base = LEVEL_DIFF_MATRIX[bucket]
    for key, multiplier in base.items():
        if key not in weights:
            continue
        softened = 1.0 + (float(multiplier) - 1.0) * factor
        weights[key] = max(0.0, float(weights[key]) / max(0.01, float(multiplier)) * softened)


def _redistribute_excess(weights: dict, source_key: Any, cap_probability: float, preferred: tuple[Any, ...]) -> None:
    total = sum(max(0.0, float(v)) for v in weights.values())
    source = max(0.0, float(weights.get(source_key, 0.0)))
    if total <= 0 or source <= 0:
        return
    current = source / total
    if current <= cap_probability:
        return
    desired = total * max(0.0, min(1.0, cap_probability))
    excess = source - desired
    weights[source_key] = desired
    targets = [k for k in preferred if k in weights and k != source_key and weights.get(k, 0.0) > 0]
    if not targets:
        targets = [k for k in weights if k != source_key and weights.get(k, 0.0) > 0]
    target_total = sum(max(0.0, float(weights[k])) for k in targets)
    if target_total <= 0:
        return
    for key in targets:
        weights[key] += excess * (max(0.0, float(weights[key])) / target_total)


def _apply_realism_caps(weights: dict, pitch: str, batter_level: int, bowler_level: int,
                        confidence: float, balls_faced: int, wickets_this_over: int) -> None:
    """Small final probability guardrails. This does not alter the game flow.

    It stops fresh batters from spraying boundaries immediately, and keeps
    wicket clusters rare while preserving a real advantage when the matchup
    is genuinely one-sided.
    """
    pitch_name = str(pitch or "even").strip().lower()
    diff = int(bowler_level or 0) - int(batter_level or 0)

    # Wicket probability per delivery. A 7-10 level bowler edge is meaningful
    # but still only a rare path to a second/third wicket in the same over.
    if wickets_this_over >= 3:
        wicket_cap = 0.0
    elif wickets_this_over == 2:
        wicket_cap = 0.018 if diff >= 7 else 0.010
    elif wickets_this_over == 1:
        wicket_cap = 0.055 if diff >= 7 else 0.038
    else:
        if pitch in {"flat", "hard"}:
            wicket_cap = 0.042 if diff >= 7 else 0.030
        elif pitch in {"green", "dusty", "dry", "slow", "bouncy"}:
            wicket_cap = 0.080 if diff >= 7 else 0.060
        else:
            wicket_cap = 0.060 if diff >= 7 else 0.045

    # Confidence reduces how much raw level mismatch should matter.
    if balls_faced >= 45:
        wicket_cap *= 0.85
    elif balls_faced >= 30:
        wicket_cap *= 0.90
    elif balls_faced >= 15:
        wicket_cap *= 0.95
    _redistribute_excess(weights, "W", wicket_cap, (0, 1, 2, 4, 6))

    # Fresh batters are not allowed to access death-over style boundary mass
    # immediately. As they settle, the cap opens progressively.
    if balls_faced <= 5:
        boundary_cap = 0.22 if pitch in {"green", "dusty", "dry", "slow", "bouncy"} else 0.27
    elif balls_faced <= 15:
        boundary_cap = 0.29 if pitch in {"green", "dusty", "dry", "slow", "bouncy"} else 0.34
    elif balls_faced <= 30:
        boundary_cap = 0.39
    elif balls_faced <= 45:
        boundary_cap = 0.46
    else:
        boundary_cap = 0.52

    total = sum(max(0.0, float(v)) for v in weights.values())
    boundary_mass = max(0.0, float(weights.get(4, 0.0))) + max(0.0, float(weights.get(6, 0.0)))
    current_boundary = (boundary_mass / total) if total > 0 else 0.0
    if current_boundary > boundary_cap and boundary_mass > 0 and total > 0:
        desired_boundary = total * boundary_cap
        scale_boundary = desired_boundary / boundary_mass
        old_boundary = boundary_mass
        weights[4] = max(0.0, float(weights.get(4, 0.0))) * scale_boundary
        weights[6] = max(0.0, float(weights.get(6, 0.0))) * scale_boundary
        excess = old_boundary - desired_boundary
        normal_keys = [0, 1, 2, 3, "WD", "NB", "LB", "BY"]
        normal_total = sum(max(0.0, float(weights.get(k, 0.0))) for k in normal_keys)
        if normal_total > 0:
            for key in normal_keys:
                share = max(0.0, float(weights.get(key, 0.0))) / normal_total
                weights[key] = max(0.0, float(weights.get(key, 0.0))) + excess * share


def resolve_weights(
    bowler_tactic: str,
    batter_mindset: str,
    pitch: str,
    over_number: int,
    batter_level: int,
    bowler_level: int,
    confidence: float,
    batsman_balls_faced: int = 0,
    wickets_this_over: int = 0,
    bowler_style: str | None = None,
    bowler_role: str | None = None,
) -> dict:
    """Runs the full 6-layer algorithm and returns final weights, ready
    for a weighted-random pick."""
    mindset = batter_mindset if batter_mindset in BASE_WEIGHTS_BY_MINDSET else "neutral"
    tactic = bowler_tactic if bowler_tactic in BOWLER_TACTICS else "swinging"
    tactic_profile = _tactic_archetype(tactic)

    weights = dict(BASE_WEIGHTS_BY_MINDSET[mindset])

    tactical = TACTICAL_MODIFIERS.get((tactic_profile, mindset)) or TACTICAL_MODIFIERS[("swinging", "neutral")]
    _apply(weights, tactical)

    _apply(weights, PITCH_MATRIX.get(pitch, {}))
    pitch_match = _apply_pitch_edge(weights, pitch, batsman_balls_faced, bowler_style, bowler_role)
    _apply(weights, PHASE_MATRIX[phase_key(over_number)])
    _apply(weights, LEVEL_DIFF_MATRIX[level_bucket(batter_level, bowler_level)])

    zone = confidence_zone(confidence)
    zone_mult = dict(CONFIDENCE_ZONE_MATRIX[zone])
    if zone == "in_the_zone" and mindset in ("ultra_aggressive", "aggressive") and tactic == "variation":
        zone_mult["W"] = VARIATION_COUNTER_WICKET_MULT
    _apply(weights, zone_mult)

    # Once a batter has seen the attack for a while, raw level mismatch
    # matters less than actual execution and confidence.
    _dampen_level_advantage(weights, batter_level, bowler_level, batsman_balls_faced)

    if pitch_match:
        _cap_early_pitch_wicket_risk(weights, batsman_balls_faced)
        _cap_early_pitch_boundary_pressure(weights, batsman_balls_faced)

    _apply_realism_caps(
        weights, pitch, batter_level, bowler_level, confidence,
        batsman_balls_faced, int(wickets_this_over or 0),
    )

    return {key: max(0.0, value) for key, value in weights.items()}


def _cap_early_pitch_boundary_pressure(weights: dict, batsman_balls_faced: int) -> None:
    """On a correctly matched bowling-friendly pitch, keep a fresh batter
    from converting too much of an aggressive intent into easy boundaries.
    The later bands are progressively relaxed so a set batter can still score
    at the normal profile-driven rate."""
    balls = max(0, int(batsman_balls_faced or 0))
    cap = 0.30 if balls <= 5 else (0.38 if balls <= 15 else (0.43 if balls <= 30 else (0.46 if balls <= 45 else 0.48)))
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    boundary_mass = max(0.0, float(weights.get(4, 0.0))) + max(0.0, float(weights.get(6, 0.0)))
    current = boundary_mass / total
    if current <= cap or boundary_mass <= 0:
        return
    desired = cap * total
    scale_boundary = desired / boundary_mass
    excess = boundary_mass - desired
    for key in (4, 6):
        weights[key] = max(0.0, float(weights.get(key, 0.0))) * scale_boundary
    # Put the suppressed attacking mass mostly back into normal cricket
    # outcomes rather than artificially creating more wickets.
    normal_keys = [0, 1, 2, 3]
    normal_total = sum(max(0.0, float(weights.get(k, 0.0))) for k in normal_keys)
    if normal_total > 0:
        for key in normal_keys:
            share = max(0.0, float(weights.get(key, 0.0))) / normal_total
            weights[key] = max(0.0, float(weights.get(key, 0.0))) + excess * share



def simulate(bowler_tactic: str, batter_mindset: str, pitch: str, over_number: int,
             batter_level: int, bowler_level: int, confidence: float,
             batsman_balls_faced: int = 0, wickets_this_over: int = 0,
             bowler_style: str | None = None, bowler_role: str | None = None):
    """Returns one sampled outcome code from OUTCOMES."""
    weights = resolve_weights(
        bowler_tactic, batter_mindset, pitch, over_number, batter_level, bowler_level, confidence,
        batsman_balls_faced=batsman_balls_faced,
        wickets_this_over=wickets_this_over,
        bowler_style=bowler_style, bowler_role=bowler_role,
    )
    values = [weights.get(key, 0.0) for key in OUTCOMES]
    return random.choices(OUTCOMES, weights=values, k=1)[0]
