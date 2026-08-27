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
    # Recalibrated run environment: batting-friendly surfaces now sit higher,
    # while bowling-friendly surfaces retain their wicket/dot identity.
    "green": {"W": 1.52, 0: 1.25, 4: 0.72, 6: 0.58, 1: 0.98, "WD": 1.08},
    "dusty": {"W": 1.48, 0: 1.23, 1: 1.07, 4: 0.70, 6: 0.58, "BY": 1.28},
    "dry": {1: 1.08, 2: 1.05, 4: 0.76, 6: 0.68, "W": 1.32, 0: 1.06},
    "hard": {4: 1.35, 6: 1.28, 0: 0.82, 1: 1.02, 2: 1.04, "W": 1.05, "NB": 1.18},
    "flat": {4: 1.65, 6: 1.75, 0: 0.55, 1: 1.02, 2: 1.08, "W": 0.50, "WD": 0.90},
    "bouncy": {6: 1.00, "W": 1.38, 0: 1.18, 1: 0.92, 4: 0.80, "LB": 1.18},
    "slow": {0: 1.20, 1: 1.07, 4: 0.70, 6: 0.64, "W": 1.30},
    "even": {0: 0.96, 1: 1.02, 2: 1.03, 4: 1.05, 6: 1.05, "W": 1.03},
}

# Soft first-innings score environment targets. These are NOT hard score caps.
# Defensive/rotate-heavy innings can still finish below the lower band.
# The engine uses these as a mild run-environment nudge only.
PITCH_SCORE_BANDS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "flat": ((185, 235), (235, 270)),
    "hard": ((175, 215), (215, 240)),
    "even": ((150, 180), (180, 220)),
    "green": ((130, 165), (165, 195)),
    "dusty": ((125, 165), (165, 205)),
    "dry": ((135, 175), (175, 210)),
    "slow": ((125, 165), (165, 200)),
    "bouncy": ((140, 175), (175, 215)),
}


def _phase_modifiers_for_over(over_number: int) -> dict:
    """Use the existing death profile in the final over, while softening
    the death-over hitting boost in overs 16-19. This keeps the existing
    20th-over finish intact and avoids making the whole death phase a
    continuous boundary surge."""
    phase = phase_key(over_number)
    if phase != "overs_16_20" or int(over_number or 0) >= 20:
        return PHASE_MATRIX[phase]

    base = dict(PHASE_MATRIX[phase])
    # Pull only the death amplification part 55% back toward neutral.
    # Wicket pressure is also softened, but retained.
    soften = {6: 0.70, 4: 0.76, 0: 0.86, 1: 0.98, 2: 0.98, "W": 0.82, "NB": 0.96, "WD": 0.96}
    return {k: (1.0 + (float(v) - 1.0) * soften.get(k, 0.90)) for k, v in base.items()}


def _apply_pitch_hitting_dampening(weights: dict, pitch: str) -> None:
    """Small pitch-aware dampening of routine boundary conversion.

    This does not replace PITCH_MATRIX. It only transfers a little 4/6 mass
    into dots/singles so batting-friendly pitches do not become runaway
    scoring environments while bowling-friendly pitches keep their identity.
    """
    name = str(pitch or "even").strip().lower()
    reduction = {
        "flat": 0.16, "hard": 0.12, "even": 0.10,
        "bouncy": 0.07, "dry": 0.07, "green": 0.06,
        "dusty": 0.06, "slow": 0.06,
    }.get(name, 0.08)
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    boundary = max(0.0, float(weights.get(4, 0.0))) + max(0.0, float(weights.get(6, 0.0)))
    if boundary <= 0:
        return
    moved = min(boundary * reduction, boundary * 0.22)
    if moved <= 0:
        return
    shares = {4: (max(0.0, float(weights.get(4, 0.0))) / boundary),
              6: (max(0.0, float(weights.get(6, 0.0))) / boundary)}
    weights[4] = max(0.0, float(weights.get(4, 0.0))) - moved * shares[4]
    weights[6] = max(0.0, float(weights.get(6, 0.0))) - moved * shares[6]
    # Prefer ordinary rotation, with the remainder going to dots.
    weights[1] = max(0.0, float(weights.get(1, 0.0))) + moved * 0.72
    weights[0] = max(0.0, float(weights.get(0, 0.0))) + moved * 0.28


def _apply_over_run_rarity(weights: dict, over_runs: int, high_run_overs: int, very_high_run_overs: int) -> None:
    """Keep 18+ and 20+ run overs uncommon without a hard inning score cap.

    Once an over is already productive, the final balls are gently de-risked.
    A completed 18+ over makes repeat high overs less likely, and a completed
    20+ over makes another explosive over rarer still.
    """
    current = max(0, int(over_runs or 0))
    high = max(0, int(high_run_overs or 0))
    extreme = max(0, int(very_high_run_overs or 0))

    repeat_scale = (0.75 ** high) * (0.45 ** extreme)
    if repeat_scale < 1.0:
        for key in (4, 6):
            old = max(0.0, float(weights.get(key, 0.0)))
            weights[key] = old * repeat_scale
            freed = old - weights[key]
            weights[1] = max(0.0, float(weights.get(1, 0.0))) + freed * 0.70
            weights[0] = max(0.0, float(weights.get(0, 0.0))) + freed * 0.30

    if current >= 18:
        score_scale = 0.03
    elif current >= 15:
        score_scale = 0.15
    elif current >= 12:
        score_scale = 0.30
    elif current >= 8:
        score_scale = 0.55
    else:
        score_scale = 1.0

    if score_scale >= 1.0:
        return

    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    score_keys = (1, 2, 3, 4, 6, "WD", "NB", "LB", "BY")
    source = sum(max(0.0, float(weights.get(k, 0.0))) for k in score_keys)
    if source <= 0:
        return
    reduction = source * (1.0 - score_scale)
    for key in score_keys:
        old = max(0.0, float(weights.get(key, 0.0)))
        weights[key] = old * score_scale
    weights[0] = max(0.0, float(weights.get(0, 0.0))) + reduction * 0.80
    weights[1] = max(0.0, float(weights.get(1, 0.0))) + reduction * 0.20



def pitch_target_rpo(pitch: str) -> float:
    name = str(pitch or "even").strip().lower()
    bands = PITCH_SCORE_BANDS.get(name)
    if not bands:
        return 9.0
    low_band, high_band = bands
    low_mid = (low_band[0] + low_band[1]) / 2.0
    high_mid = (high_band[0] + high_band[1]) / 2.0
    return ((low_mid + high_mid) / 2.0) / 20.0

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
            0: 1.07, 1: 1.04, 2: 1.02, 3: 0.98,
            4: 0.90, 6: 0.82, "W": 1.06,
        })
    elif balls <= 30:
        _apply(weights, {0: 1.03, 4: 0.95, 6: 0.92, "W": 1.04})
    elif balls <= 45:
        _apply(weights, {0: 1.02, 4: 0.97, 6: 0.94, "W": 1.03})
    else:
        _apply(weights, {0: 1.01, 4: 0.98, 6: 0.95, "W": 1.02})
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


def _apply_pitch_score_environment(weights: dict, pitch: str, is_second_innings: bool = False) -> None:
    """Gently pull the ball-outcome distribution toward the pitch's desired
    first-innings scoring environment without imposing a hard score cap.
    The current tactical/pitch/phase choices still dominate each ball.
    """
    target_rpo = pitch_target_rpo(pitch)
    # The chase has no pressure penalty. This is only a softer run-environment
    # target for the second innings, capped at the requested 12.12 RPO.
    if is_second_innings:
        target_rpo = min(12.12, target_rpo)
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    expected = sum((k if isinstance(k, int) else (1.0 if k in {"WD", "NB", "LB", "BY"} else 0.0)) * max(0.0, float(v)) for k, v in weights.items()) / total
    gap = target_rpo - expected
    # Strong enough to stop runaway scoring, but still a soft nudge rather
    # than a score clamp. Defensive/rotate intent can naturally finish below
    # the pitch band, and aggressive intent still has room to beat it.
    if abs(gap) < 0.20:
        return
    strength = min(0.24, 0.045 + abs(gap) * 0.085)
    probs = {k: max(0.0, float(v)) / total for k, v in weights.items()}
    scoring = (1, 2, 4, 6, "WD", "NB", "LB", "BY")
    containment = (0, "W")

    if gap > 0:
        source = sum(probs.get(k, 0.0) for k in containment)
        moved = min(strength, source * 0.42)
        if moved <= 0:
            return
        for k in containment:
            share = probs.get(k, 0.0) / source if source else 0.0
            probs[k] = max(0.0, probs.get(k, 0.0) - moved * share)
        score_total = sum(probs.get(k, 0.0) for k in scoring)
        if score_total > 0:
            for k in scoring:
                probs[k] += moved * (probs.get(k, 0.0) / score_total)
    else:
        source = sum(probs.get(k, 0.0) for k in scoring)
        moved = min(strength, source * 0.30)
        if moved <= 0:
            return
        for k in scoring:
            share = probs.get(k, 0.0) / source if source else 0.0
            probs[k] = max(0.0, probs.get(k, 0.0) - moved * share)
        contain_total = sum(probs.get(k, 0.0) for k in containment)
        if contain_total > 0:
            probs[0] += moved * (probs.get(0, 0.0) / contain_total)
            probs["W"] += moved * (probs.get("W", 0.0) / contain_total)

    for k in list(weights):
        weights[k] = max(0.0, probs.get(k, 0.0) * total)


# --- LAYER 5: SMOOTH BOWLER LEVEL vs BATTER LEVEL ---
# Test build: replace the old hard buckets with a smooth level-gap model.
# Levels are treated from 1..99. A zero gap is neutral. Gaps 1..5 add a
# one-percentage-point matchup budget per level. From gap 6 onward the
# theoretical index accelerates (6 -> 1.10, 7 -> 1.20, ...), while the actual
# probability transfer is softened and capped so the game stays balanced.
LEVEL_MIN = 1
LEVEL_MAX = 99
LEVEL_GAP_MAX_BUDGET = 0.10  # slightly stronger but still bounded matchup transfer


def _clamp_level(value: int) -> int:
    return max(LEVEL_MIN, min(LEVEL_MAX, int(value or 0)))


def level_gap(batter_level: int, bowler_level: int) -> int:
    return _clamp_level(batter_level) - _clamp_level(bowler_level)


def theoretical_level_index(abs_gap: int) -> float:
    gap = max(0, int(abs_gap or 0))
    if gap <= 5:
        return 1.0 + 0.01 * gap
    return 1.10 + 0.10 * (gap - 6)


def level_advantage_budget(abs_gap: int) -> float:
    gap = max(0, int(abs_gap or 0))
    if gap <= 5:
        budget = 0.01 * gap
    else:
        # Smooth actual effect: 6->6%, 10->10%, 20->20%, then capped.
        budget = min(LEVEL_GAP_MAX_BUDGET, 0.065 + 0.010 * (gap - 6))
    return max(0.0, min(LEVEL_GAP_MAX_BUDGET, budget))


# --- LAYER 6: CONFIDENCE SYSTEM ---
# Confidence is deliberately a small finishing touch. It improves conversion
# for a set batter, but never turns a batter into a shield against wickets.
CONFIDENCE_ENGINE_ENABLED = True


def confidence_zone(confidence: float) -> str:
    value = max(0.0, min(100.0, float(confidence or 0.0)))
    if value < 25:
        return "nervous"
    if value < 60:
        return "building"
    if value < 80:
        return "set"
    return "in_the_zone"


def _apply(weights: dict, modifiers: dict) -> None:
    for key, mult in modifiers.items():
        weights[key] = weights.get(key, 0.0) * mult


def _dampen_level_advantage(
    weights: dict,
    batter_level: int,
    bowler_level: int,
    balls_faced: int,
    confidence: float,
) -> None:
    if not CONFIDENCE_ENGINE_ENABLED:
        return
    # A confident batter reduces the bowler's raw level edge very slightly;
    # this is capped so confidence never erases a genuine skill mismatch.
    if confidence >= 70 and bowler_level > batter_level:
        _move_probability_mass(weights, "W", 1, min(0.012, (confidence - 65) / 2500))


def _apply_confidence_micro(weights: dict, confidence: float, balls_faced: int) -> None:
    """Soft confidence conversion; never applies a bowler-heavy penalty."""
    if not CONFIDENCE_ENGINE_ENABLED:
        return
    value = max(0.0, min(100.0, float(confidence or 0.0)))
    if value < 25:
        _apply(weights, {0: 1.012, "W": 1.006, 4: 0.994, 6: 0.990})
    elif value >= 80 and balls_faced >= 10:
        _apply(weights, {1: 1.009, 2: 1.009, 4: 1.017, 6: 1.013, "W": 0.993})
    elif value >= 60 and balls_faced >= 6:
        _apply(weights, {1: 1.006, 2: 1.006, 4: 1.009, 6: 1.005})


def _apply_early_collapse_pressure(
    weights: dict,
    batter_level: int,
    batter_role: str | None,
    batting_position: int,
    wickets_in_hand: int,
    balls_remaining: int,
    confidence: float,
) -> None:
    """Small early-collapse pressure for genuinely lower-order batters.

    It is activated only when six or more wickets have already fallen within
    the first seven overs (42 legal balls). Position 7+ or a bowler-role batter
    gets the full tail pressure. All-rounders receive only a lighter pressure.
    Confidence gradually removes the extra pressure, returning the batter to
    normal probabilities as they settle.
    """
    balls_remaining = max(0, int(balls_remaining or 0))
    wickets_lost = max(0, 10 - int(wickets_in_hand or 0))
    balls_played = max(0, 120 - balls_remaining)
    if balls_played > 42 or wickets_lost < 6:
        return

    role = str(batter_role or "").strip().lower().replace("-", "_").replace(" ", "_")
    is_bowler = "bowler" in role and "all" not in role
    is_allrounder = "all_round" in role or "allrounder" in role
    is_lower = int(batting_position or 1) >= 7 or is_bowler
    if not is_lower and not is_allrounder:
        return

    # Full pressure for bowlers / position 7+, half-strength for all-rounders.
    strength = 1.0 if is_lower else 0.45
    # Confidence removes the special collapse pressure smoothly.
    conf = max(0.0, min(100.0, float(confidence or 0.0)))
    confidence_factor = max(0.0, 1.0 - conf / 100.0)
    strength *= confidence_factor
    if strength <= 0:
        return

    dot_add = 0.018 * strength
    wicket_add = 0.010 * strength
    single_cut = 0.012 * strength
    boundary_cut = 0.010 * strength

    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return

    # Fund pressure from singles and boundaries only.
    probs = {k: max(0.0, float(v)) / total for k, v in weights.items()}
    single_available = probs.get(1, 0.0)
    boundary_available = probs.get(4, 0.0) + probs.get(6, 0.0)
    moved = min(single_cut, single_available)
    boundary_move = min(boundary_cut, boundary_available)
    total_move = moved + boundary_move
    wanted = dot_add + wicket_add
    if total_move <= 0 or wanted <= 0:
        return

    scale = min(1.0, total_move / wanted)
    dot_add *= scale
    wicket_add *= scale

    probs[1] = max(0.0, probs.get(1, 0.0) - moved)
    if boundary_move > 0 and boundary_available > 0:
        for key in (4, 6):
            share = probs.get(key, 0.0) / boundary_available
            probs[key] = max(0.0, probs.get(key, 0.0) - boundary_move * share)
    probs[0] = probs.get(0, 0.0) + dot_add
    probs["W"] = probs.get("W", 0.0) + wicket_add

    new_total = sum(max(0.0, v) for v in probs.values())
    if new_total <= 0:
        return
    for key in list(weights):
        weights[key] = max(0.0, probs.get(key, 0.0) * total / new_total)


def _apply_match_state(
    weights: dict,
    target: int | None,
    total_runs: int,
    balls_remaining: int,
    wickets_in_hand: int,
    batting_position: int,
) -> None:
    """Chase information is retained for display/state only; it does not
    apply a scoring or wicket penalty to the chasing side."""
    return


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

    # Wicket cap remains pitch-aware, but now tracks every level of matchup
    # advantage so a 1-level bowler edge is not flattened to the same cap as
    # an even matchup. The effect is deliberately bounded for balance.
    level_gap_for_cap = max(0, int(diff or 0))
    if pitch in {"flat", "hard"}:
        base_cap = 0.030
        gap_step = 0.0015
        max_cap = 0.045
    elif pitch in {"green", "dusty", "dry", "slow", "bouncy"}:
        base_cap = 0.060
        gap_step = 0.0020
        max_cap = 0.080
    else:
        base_cap = 0.045
        gap_step = 0.0020
        max_cap = 0.060

    matchup_cap = min(max_cap, base_cap + gap_step * level_gap_for_cap)
    if wickets_this_over >= 3:
        wicket_cap = 0.0
    elif wickets_this_over == 2:
        wicket_cap = min(0.018, matchup_cap * 0.30)
    elif wickets_this_over == 1:
        wicket_cap = min(0.055, matchup_cap * 0.82)
    else:
        wicket_cap = matchup_cap

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


# ---------------------------------------------------------------------------
# ISOLATED MICRO-MODIFIERS
# ---------------------------------------------------------------------------
# Tailender calibration is intentionally isolated from the existing tactical
# matrices. It only applies when the batter's own level is below 50.
#   41-49: clear lower-order pressure
#   40-44: stronger dismissal pressure
#   <40:   strongest tailender penalty; boundaries become exceptional
# The transfer preserves the existing architecture and leaves 50+ batters
# completely unchanged. Singles are mildly reduced, while dot/wicket mass is
# funded primarily from singles + boundaries so the effect is a true
# lower-order profile rather than a blanket global wicket multiplier.
# ---------------------------------------------------------------------------

TAILENDER_BAND_RULES: dict[str, dict[str, float]] = {
    "soft_tail": {"min_level": 50, "dot": 0.035, "wicket": 0.010, "single": 0.018, "boundary": 0.020},
    "lower_order": {"min_level": 45, "dot": 0.14, "wicket": 0.028, "single": 0.060, "boundary": 0.050},
    "tailender": {"min_level": 40, "dot": 0.19, "wicket": 0.045, "single": 0.080, "boundary": 0.035},
    "deep_tail": {"min_level": 35, "dot": 0.25, "wicket": 0.075, "single": 0.110, "boundary": 0.025},
    "extreme_tail": {"min_level": -10**9, "dot": 0.31, "wicket": 0.090, "single": 0.125, "boundary": 0.012},
}


def _tailender_band(batter_level: int) -> str | None:
    level = int(batter_level or 0)
    if level >= 52:
        return None
    if level >= 50:
        return "soft_tail"
    if level >= 45:
        return "lower_order"
    if level >= 40:
        return "tailender"
    if level >= 35:
        return "deep_tail"
    return "extreme_tail"


def _move_probability_mass(weights: dict, source_key, target_key, amount: float) -> None:
    """Move a normalized amount of probability mass between two outcomes."""
    amount = max(0.0, float(amount or 0.0))
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0 or amount <= 0:
        return
    source = max(0.0, float(weights.get(source_key, 0.0)))
    available = source / total
    moved = min(amount, available)
    if moved <= 0:
        return
    weights[source_key] = source - (moved * total)
    weights[target_key] = max(0.0, float(weights.get(target_key, 0.0))) + (moved * total)


def _apply_defensive_adaptation_micro(
    weights: dict,
    batter_level: int,
    bowler_level: int,
    balls_faced: int,
    confidence: float,
) -> None:
    """Defensive adaptation based on balls faced only; confidence disabled."""
    balls = max(0, int(balls_faced or 0))
    if balls <= 5:
        return
    single_gain = 0.045
    if balls > 15:
        single_gain += 0.055
    if balls > 30:
        single_gain += 0.060
    _move_probability_mass(weights, 0, 1, single_gain)

    diff = int(bowler_level or 0) - int(batter_level or 0)
    if diff >= 11:
        _move_probability_mass(weights, 1, 0, min(0.035, 0.020 + (diff - 11) * 0.001))
    elif diff <= -11:
        _move_probability_mass(weights, 0, 1, min(0.035, 0.020 + (abs(diff) - 11) * 0.001))

    if balls <= 30:
        return
    total = sum(max(0.0, float(v)) for v in weights.values())
    boundary_mass = max(0.0, float(weights.get(4, 0.0))) + max(0.0, float(weights.get(6, 0.0)))
    if total <= 0 or boundary_mass <= 0:
        return
    cap = total * 0.025
    if boundary_mass <= cap:
        return
    excess = boundary_mass - cap
    scale = cap / boundary_mass
    weights[4] = max(0.0, float(weights.get(4, 0.0))) * scale
    weights[6] = max(0.0, float(weights.get(6, 0.0))) * scale
    weights[1] = max(0.0, float(weights.get(1, 0.0))) + excess


def _apply_tailender_micro(weights: dict, batter_level: int) -> None:
    """Make sub-50 batters behave like lower-order/tail players without
    replacing any existing tactic, pitch, phase, level, confidence, or cap
    logic. The requested mass is funded from singles and boundaries only.
    """
    band = _tailender_band(batter_level)
    if band is None:
        return
    rule = TAILENDER_BAND_RULES[band]

    clean = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        return
    probs = {k: v / total for k, v in clean.items()}

    # Move a modest amount out of singles and boundary outcomes. This keeps
    # normal cricket scoring available, but makes lower-order survival much
    # less comfortable.
    requested_dot = rule["dot"]
    requested_wicket = rule["wicket"]
    requested_single = rule["single"]
    requested_boundary = rule["boundary"]

    source_single = probs.get(1, 0.0)
    source_boundary = probs.get(4, 0.0) + probs.get(6, 0.0)
    requested_from_single = min(requested_single, source_single * 0.75)
    requested_from_boundary = min(requested_boundary, source_boundary * 0.80)
    available = requested_from_single + requested_from_boundary
    desired_add = requested_dot + requested_wicket
    if desired_add <= 0 or available <= 0:
        return

    scale = min(1.0, available / desired_add)
    dot_add = requested_dot * scale
    wicket_add = requested_wicket * scale

    # Fund the new dot/wicket mass proportionally from singles and boundaries.
    move_single = available * (requested_from_single / max(1e-12, available))
    move_boundary = available * (requested_from_boundary / max(1e-12, available))

    probs[1] = max(0.0, probs.get(1, 0.0) - move_single)
    if source_boundary > 0 and move_boundary > 0:
        for key in (4, 6):
            share = probs.get(key, 0.0) / source_boundary
            probs[key] = max(0.0, probs.get(key, 0.0) - move_boundary * share)

    probs[0] = probs.get(0, 0.0) + dot_add
    probs["W"] = probs.get("W", 0.0) + wicket_add

    # Renormalize to protect against floating-point drift. No other outcome
    # is directly altered, and levels >=50 never enter this function.
    new_total = sum(max(0.0, v) for v in probs.values())
    if new_total <= 0:
        return
    for key in list(weights):
        weights[key] = max(0.0, probs.get(key, 0.0) * total / new_total)

    # Tailenders may occasionally nick a boundary, but they should not
    # inherit the hitting profile of a proper batter from the generic
    # mindset/phase matrices. Enforce a small, explicit ceiling after all
    # earlier layers have run.
    boundary_cap = {
        "soft_tail": 0.10,
        "lower_order": 0.075,
        "tailender": 0.055,
        "deep_tail": 0.040,
        "extreme_tail": 0.025,
    }.get(band, 0.025)
    final_total = sum(max(0.0, float(v)) for v in weights.values())
    boundary_mass = max(0.0, float(weights.get(4, 0.0))) + max(0.0, float(weights.get(6, 0.0)))
    cap_mass = final_total * boundary_cap
    if boundary_mass > cap_mass and boundary_mass > 0:
        excess = boundary_mass - cap_mass
        scale = cap_mass / boundary_mass
        weights[4] = max(0.0, float(weights.get(4, 0.0))) * scale
        weights[6] = max(0.0, float(weights.get(6, 0.0))) * scale
        weights[0] = max(0.0, float(weights.get(0, 0.0))) + excess * 0.80
        weights[1] = max(0.0, float(weights.get(1, 0.0))) + excess * 0.20

# These helpers intentionally sit outside the existing tactic/pitch/phase/
# level/confidence matrices. They do not rewrite or mutate those tables.
# They only make small, final probability adjustments for two gameplay rules:
#   1) fresh + Ultra Aggressive risk, and
#   2) a six-tier level-gap expression with a small death-over lift.
#
# The transfer is done on normalized probability mass so single-run/single
# probability is preserved exactly when the requested transfer is feasible.
# ---------------------------------------------------------------------------

FRESH_ULTRA_RISK: dict[str, dict[str, tuple[float, float]]] = {
    # (dot probability points, wicket probability points)
    "overs_1_6": {
        "green": (0.045, 0.018),
        "bouncy": (0.040, 0.016),
        "dusty": (0.035, 0.014),
        "slow": (0.035, 0.014),
        "dry": (0.025, 0.011),
        "even": (0.015, 0.007),
        "hard": (0.015, 0.007),
        "flat": (0.005, 0.003),
    },
    "overs_7_15": {
        "green": (0.040, 0.017),
        "bouncy": (0.035, 0.015),
        "dusty": (0.032, 0.013),
        "slow": (0.032, 0.013),
        "dry": (0.022, 0.010),
        "even": (0.012, 0.006),
        "hard": (0.012, 0.006),
        "flat": (0.005, 0.003),
    },
    "overs_16_20": {
        "green": (0.055, 0.023),
        "bouncy": (0.050, 0.021),
        "dusty": (0.045, 0.019),
        "slow": (0.045, 0.019),
        "dry": (0.035, 0.015),
        "even": (0.025, 0.011),
        "hard": (0.025, 0.011),
        "flat": (0.015, 0.007),
    },
}

LEVEL_GAP_TIERS: tuple[tuple[int, int, str]] = (
    (-10**9, -31, "huge_bowler"),
    (-30, -16, "bowler"),
    (-15, -1, "slight_bowler"),
    (0, 15, "slight_batter"),
    (16, 30, "batter"),
    (31, 10**9, "huge_batter"),
)

LEVEL_GAP_MICRO: dict[str, tuple[float, float, float]] = {
    # (dot points, wicket points, boundary points)
    "huge_bowler": (0.018, 0.006, 0.022),
    "bowler": (0.012, 0.004, 0.015),
    "slight_bowler": (0.005, 0.002, 0.007),
    "slight_batter": (-0.005, -0.002, 0.007),
    "batter": (-0.012, -0.004, 0.015),
    "huge_batter": (-0.018, -0.006, 0.022),
}


def _level_gap_tier(batter_level: int, bowler_level: int) -> str | None:
    diff = int(batter_level or 0) - int(bowler_level or 0)
    for low, high, name in LEVEL_GAP_TIERS:
        if low <= diff <= high:
            return name
    return None


def _transfer_probability_mass(
    weights: dict,
    *,
    add_dot: float = 0.0,
    add_wicket: float = 0.0,
    add_boundary: float = 0.0,
) -> None:
    """Transfer normalized probability mass without touching singles.

    Positive dot/wicket transfers are funded from boundary mass. Positive
    boundary transfers are funded from dot/wicket mass. The requested
    transfer is scaled down automatically if insufficient source mass exists.
    """
    clean = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        return
    probs = {k: v / total for k, v in clean.items()}

    # Positive boundary demand comes from dot + wicket, and positive
    # dot/wicket demand comes from boundary. This deliberately leaves singles
    # and extras out of the transfer pool.
    dot = probs.get(0, 0.0)
    wicket = probs.get("W", 0.0)
    boundary_keys = (4, 6)
    boundary = sum(probs.get(k, 0.0) for k in boundary_keys)

    if add_dot > 0 or add_wicket > 0:
        requested = max(0.0, add_dot) + max(0.0, add_wicket)
        available = boundary
        scale = min(1.0, available / requested) if requested > 0 else 0.0
        dot_add = max(0.0, add_dot) * scale
        wicket_add = max(0.0, add_wicket) * scale
        moved = dot_add + wicket_add
        probs[0] = dot + dot_add
        probs["W"] = wicket + wicket_add
        if boundary > 0 and moved > 0:
            for key in boundary_keys:
                share = probs.get(key, 0.0) / boundary
                probs[key] = max(0.0, probs.get(key, 0.0) - moved * share)

    elif add_boundary > 0:
        source = dot + wicket
        moved = min(max(0.0, add_boundary), source)
        if source > 0 and moved > 0:
            probs[0] = max(0.0, dot - moved * (dot / source))
            probs["W"] = max(0.0, wicket - moved * (wicket / source))
            if boundary > 0:
                for key in boundary_keys:
                    share = probs.get(key, 0.0) / boundary
                    probs[key] = probs.get(key, 0.0) + moved * share
            else:
                probs[4] = moved * 0.75
                probs[6] = moved * 0.25

    # Write the probabilities back at the same overall scale.
    for key in list(weights):
        weights[key] = max(0.0, probs.get(key, 0.0) * total)


def _apply_fresh_ultra_micro(weights: dict, pitch: str, over_number: int, balls_faced: int, mindset: str) -> None:
    if mindset != "ultra_aggressive":
        return
    balls = max(0, int(balls_faced or 0))
    if balls > 5:
        return
    phase = phase_key(over_number)
    pitch_name = str(pitch or "even").strip().lower()
    dot_add, wicket_add = FRESH_ULTRA_RISK.get(phase, {}).get(pitch_name, (0.0, 0.0))
    # First two balls are the full opening-risk spike; balls 3-5 retain only
    # 70% of it so the batter visibly settles without a hard discontinuity.
    if balls >= 3:
        dot_add *= 0.70
        wicket_add *= 0.70
    _transfer_probability_mass(
        weights, add_dot=dot_add, add_wicket=wicket_add,
    )


def _apply_smooth_level_gap(weights: dict, batter_level: int, bowler_level: int, over_number: int) -> None:
    """Apply a smooth, bounded level-gap advantage.

    The theoretical index follows the requested progression (1.01..1.05,
    then 1.10, 1.20, ...), while the actual game effect is a controlled
    probability-mass transfer. A stronger bowler shifts mass toward dots and
    wickets; a stronger batter gets the mirror image. Singles, doubles and
    boundary rates move gradually rather than jumping between buckets.
    """
    diff = level_gap(batter_level, bowler_level)
    if diff == 0:
        return
    budget = level_advantage_budget(abs(diff))
    if phase_key(over_number) == "overs_16_20":
        budget = min(LEVEL_GAP_MAX_BUDGET, budget * 1.05)

    if diff < 0:
        target = {0: 0.69, "W": 0.31}
        source = {1: 0.38, 2: 0.22, 4: 0.20, 6: 0.14, "WD": 0.03, "NB": 0.03}
    else:
        target = {1: 0.38, 2: 0.20, 4: 0.20, 6: 0.14, "WD": 0.04, "NB": 0.04}
        source = {0: 0.52, "W": 0.27, 4: 0.13, 6: 0.08}

    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    probs = {k: max(0.0, float(v)) / total for k, v in weights.items()}

    # Minimum floors prevent a level advantage from ever driving a core
    # outcome to zero. The existing realism layer still controls the final cap.
    floors = {0: 0.05, 1: 0.04, 2: 0.01, 4: 0.015, 6: 0.003, "W": 0.008, "WD": 0.001, "NB": 0.001}

    desired = {k: budget * share for k, share in source.items() if k in probs}
    requested_total = sum(desired.values())
    if requested_total <= 0:
        return

    # Take the requested mass from source outcomes while respecting floors.
    moved = 0.0
    available = {}
    for key, amount in desired.items():
        available[key] = max(0.0, probs.get(key, 0.0) - floors.get(key, 0.0))
    total_available = sum(available.values())
    if total_available <= 0:
        return
    scale = min(1.0, total_available / requested_total)
    for key, avail in available.items():
        take = min(avail, desired.get(key, 0.0) * scale)
        probs[key] -= take
        moved += take

    # If source floors limited the exact request, redistribute only what was
    # actually removed. This keeps the system probability-conserving.
    target_total = sum(target.values())
    if target_total <= 0 or moved <= 0:
        return
    for key, share in target.items():
        if key in probs:
            probs[key] += moved * (share / target_total)

    # Final normalization and write-back.
    total2 = sum(max(0.0, v) for v in probs.values())
    if total2 <= 0:
        return
    for key in list(weights):
        weights[key] = max(0.0, probs.get(key, 0.0) * total / total2)


def _apply_final_wicket_cap(
    weights: dict,
    pitch: str,
    batter_level: int,
    bowler_level: int,
    balls_faced: int,
    wickets_this_over: int,
) -> None:
    """Final wicket guardrail applied after all micro-layers.

    This prevents later tail/chase/level micro-rules from rebuilding a wicket
    cluster that the realism layer already capped.
    """
    pitch_name = str(pitch or "even").strip().lower()
    diff = max(0, int(bowler_level or 0) - int(batter_level or 0))
    if pitch_name in {"flat", "hard"}:
        base_cap, gap_step, max_cap = 0.030, 0.0015, 0.045
    elif pitch_name in {"green", "dusty", "dry", "slow", "bouncy"}:
        base_cap, gap_step, max_cap = 0.060, 0.0020, 0.080
    else:
        base_cap, gap_step, max_cap = 0.045, 0.0020, 0.060
    matchup_cap = min(max_cap, base_cap + gap_step * diff)
    if wickets_this_over >= 3:
        cap = 0.0
    elif wickets_this_over == 2:
        cap = min(0.018, matchup_cap * 0.30)
    elif wickets_this_over == 1:
        cap = min(0.055, matchup_cap * 0.82)
    else:
        cap = matchup_cap
    if balls_faced >= 45:
        cap *= 0.85
    elif balls_faced >= 30:
        cap *= 0.90
    elif balls_faced >= 15:
        cap *= 0.95
    _redistribute_excess(weights, "W", cap, (0, 1, 2, 4, 6))


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
    batter_role: str | None = None,
    target: int | None = None,
    total_runs: int = 0,
    balls_remaining: int = 120,
    wickets_in_hand: int = 10,
    batting_position: int = 1,
    over_runs: int = 0,
    high_run_overs: int = 0,
    very_high_run_overs: int = 0,
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
    _apply_pitch_hitting_dampening(weights, pitch)
    _apply_pitch_score_environment(weights, pitch, is_second_innings=(target is not None))
    pitch_match = _apply_pitch_edge(weights, pitch, batsman_balls_faced, bowler_style, bowler_role)
    _apply(weights, _phase_modifiers_for_over(over_number))
    if pitch_match:
        _cap_early_pitch_wicket_risk(weights, batsman_balls_faced)
        _cap_early_pitch_boundary_pressure(weights, batsman_balls_faced)

    _apply_realism_caps(
        weights, pitch, batter_level, bowler_level, confidence,
        batsman_balls_faced, int(wickets_this_over or 0),
    )

    _apply_smooth_level_gap(weights, batter_level, bowler_level, over_number)
    _apply_confidence_micro(weights, confidence, batsman_balls_faced)
    _dampen_level_advantage(
        weights, batter_level, bowler_level, batsman_balls_faced, confidence,
    )
    _apply_match_state(
        weights, target, total_runs, balls_remaining, wickets_in_hand,
        batting_position,
    )
    _apply_early_collapse_pressure(
        weights, batter_level, batter_role, batting_position, wickets_in_hand,
        balls_remaining, confidence,
    )

    # Isolated final micro-rules. The existing matrices, tactics, phase,
    # confidence, level dampening and realism caps above remain untouched.
    _apply_fresh_ultra_micro(
        weights, pitch, over_number, batsman_balls_faced, mindset,
    )
    if mindset == "defensive":
        _apply_defensive_adaptation_micro(
            weights, batter_level, bowler_level, batsman_balls_faced, confidence,
        )
    _apply_tailender_micro(weights, batter_level)
    _apply_over_run_rarity(weights, over_runs, high_run_overs, very_high_run_overs)
    _apply_final_wicket_cap(
        weights, pitch, batter_level, bowler_level, batsman_balls_faced,
        int(wickets_this_over or 0),
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
             bowler_style: str | None = None, bowler_role: str | None = None,
             batter_role: str | None = None, target: int | None = None, total_runs: int = 0,
             balls_remaining: int = 120, wickets_in_hand: int = 10,
             batting_position: int = 1, over_runs: int = 0,
             high_run_overs: int = 0, very_high_run_overs: int = 0):
    """Returns one sampled outcome code from OUTCOMES."""
    weights = resolve_weights(
        bowler_tactic, batter_mindset, pitch, over_number, batter_level, bowler_level, confidence,
        batsman_balls_faced=batsman_balls_faced,
        wickets_this_over=wickets_this_over,
        bowler_style=bowler_style, bowler_role=bowler_role, batter_role=batter_role,
        target=target, total_runs=total_runs, balls_remaining=balls_remaining,
        wickets_in_hand=wickets_in_hand, batting_position=batting_position,
        over_runs=over_runs, high_run_overs=high_run_overs, very_high_run_overs=very_high_run_overs,
    )
    values = [weights.get(key, 0.0) for key in OUTCOMES]
    return random.choices(OUTCOMES, weights=values, k=1)[0]
