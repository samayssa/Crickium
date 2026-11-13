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
  7. REALISM LAYER (runs last - see _apply_realism_layer) - the match
     situation: pitch identity and surface wear, the powerplay /
     middle / death over profile (new-ball bite, the over-7 dip, the
     squeeze, the death surge), settling-in batters, momentum inside
     the over and back-to-back wickets (collapse impact, strongest at
     the death) - while several wickets inside ONE over stay rare (a
     second is rare, a third extremely rare, a hat-trick the rarest sight
     in the game).  It keeps the dot and single probabilities exactly
     as layers 1-6 produced them and reshapes only 2s/3s/4s/6s/W/extras.
     Nothing in it depends on batting first vs chasing, or on which
     side is defending - both innings and both teams run on the same
     rules, so only the teams' own quality decides a match.  It also
     carries the batting resources (a side with wickets in hand launches,
     a side that has lost them rebuilds, and a long stand without a wicket
     slowly invites one - so innings rarely end only 3-4 down and chases
     stay alive into the last over) and the tail-ender intent risk (a slog
     gets a number 10 out quickly, rotating keeps him in).
  8. OVER-RUN GOVERNOR (_apply_over_run_rarity) - replaces the old hard
     per-over run caps (which turned the rest of an over into forced
     dots).  A hot over just gets smoothly harder to push further, the
     ceiling rises with the required run rate in a chase, and the freed
     mass goes to 2s, never to dots.  The last two overs are never capped.

engines/play_runtime.py owns the batter's confidence VALUE and the
consecutive-boundary streak (that's per-batter state, tracked on
BatterSlot) - this module is pure lookup/math, no state of its own.
"""

from __future__ import annotations

import math
import random
from typing import Any

OUTCOMES = [0, 1, 2, 3, 4, 6, "W", "WD", "NB", "LB", "BY"]

BOWLER_TACTICS = [
    "defensive", "swinging", "pace_up", "back_of_length", "variation",
                     
    "off_break", "doosra", "arm_ball", "carrom_ball", "top_spin",
                     
    "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball",
]
BATTER_MINDSETS = ["defensive", "rotate", "neutral", "aggressive", "ultra_aggressive"]

                                                   
                                                                   
                                                                  
                                                                     
                                                                      
BASE_WEIGHTS_BY_MINDSET: dict = {
    "defensive": {0: 48.0, 1: 26.0, 2: 6.0, 3: 0.8, 4: 3.5, 6: 0.3, "W": 3.0, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "rotate": {0: 22.0, 1: 40.0, 2: 14.0, 3: 1.5, 4: 4.5, 6: 0.5, "W": 3.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "neutral": {0: 28.0, 1: 32.0, 2: 14.0, 3: 1.2, 4: 9.0, 6: 2.5, "W": 4.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "aggressive": {0: 22.0, 1: 24.0, 2: 9.0, 3: 0.8, 4: 15.0, 6: 8.0, "W": 7.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
    "ultra_aggressive": {0: 14.0, 1: 16.0, 2: 5.0, 3: 0.4, 4: 19.0, 6: 17.0, "W": 12.5, "WD": 3.0, "NB": 1.0, "LB": 1.0, "BY": 0.5},
}

                                                                      
                                                                        
                                                                      
                                                               
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

                                                                      
                                                                  
                                                                    
                                                                    
                                     
MIRRORED_PAIRS = {("defensive", "defensive"), ("back_of_length", "defensive")}

                                                                        
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

                                                      
PITCH_MATRIX: dict = {
                                                                             
                                                                       
    "green": {"W": 1.52, 0: 1.25, 4: 0.72, 6: 0.58, 1: 0.98, "WD": 1.08},
    "dusty": {"W": 1.48, 0: 1.23, 1: 1.07, 4: 0.70, 6: 0.58, "BY": 1.28},
    "dry": {1: 1.08, 2: 1.05, 4: 0.76, 6: 0.68, "W": 1.32, 0: 1.06},
    "hard": {4: 1.35, 6: 1.28, 0: 0.82, 1: 1.02, 2: 1.04, "W": 1.05, "NB": 1.18},
    "flat": {4: 1.65, 6: 1.75, 0: 0.55, 1: 1.02, 2: 1.08, "W": 0.50, "WD": 0.90},
    "bouncy": {6: 1.00, "W": 1.38, 0: 1.18, 1: 0.92, 4: 0.80, "LB": 1.18},
    "slow": {0: 1.20, 1: 1.07, 4: 0.70, 6: 0.64, "W": 1.30},
    "even": {0: 0.96, 1: 1.02, 2: 1.03, 4: 1.05, 6: 1.05, "W": 1.03},
}

                                                                              
                                                                       
                                                             
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
                                                                 
    weights[1] = max(0.0, float(weights.get(1, 0.0))) + moved * 0.72
    weights[0] = max(0.0, float(weights.get(0, 0.0))) + moved * 0.28


                                                                             
                                                                           
                                                                             
                                                                             
                                                                             
                                                                           
                                                                          
                                                                
 
                                                                            
                                                     
                                                                            
                                                                      
                                                              
                                                                             
                                                                            
                
                                                                             
                                                                           
                                                                        
                                                                           
                                                            
OVER_RUN_SOFT_CEILING = 21.0
OVER_RUN_SOFTNESS = 2.6
OVER_RUN_MIN_SHOT_SCALE = 0.30
OVER_RUN_UNCAPPED_FROM_OVER = 19


def _over_run_ceiling(over_number: int, balls_faced: int, mindset: str,
                      required_rpo: float | None) -> float:
    ceiling = OVER_RUN_SOFT_CEILING
    if over_number >= 16:
        ceiling += 2.0
        if balls_faced >= 16:
            ceiling += 2.0
        if balls_faced > 30:
            ceiling += 2.0
    if mindset in ("aggressive", "ultra_aggressive"):
        ceiling += 1.5
    if required_rpo is not None and required_rpo > 10.0:
                                                                         
        ceiling += (required_rpo - 10.0) * 1.7
    return ceiling


def _apply_over_run_rarity(weights: dict, over_runs: int, high_run_overs: int, very_high_run_overs: int,
                           over_number: int = 0, batsman_balls_faced: int = 0, *,
                           mindset: str = "neutral", required_rpo: float | None = None,
                           runs_needed: int | None = None) -> None:
    """Smoothly damp 3/4/6 when the current over is already running hot.

    Only the big-shot weights are touched and the mass they lose goes to the
    2s, so P(dot) / P(single) / P(wicket) never move because of this guard
    (the old hard caps turned the rest of the over into forced dots).
    """
    if int(over_number or 0) >= OVER_RUN_UNCAPPED_FROM_OVER:
        return
    before = sum(max(0.0, float(weights.get(k, 0.0))) for k in (3, 4, 6))
    _damp_big_shots(weights, over_runs, high_run_overs, very_high_run_overs,
                    over_number, batsman_balls_faced, mindset, required_rpo, runs_needed)
    after = sum(max(0.0, float(weights.get(k, 0.0))) for k in (3, 4, 6))
    if before > after:
        weights[2] = max(0.0, float(weights.get(2, 0.0))) + (before - after)


def _damp_big_shots(weights: dict, over_runs: int, high_run_overs: int, very_high_run_overs: int,
                    over_number: int, batsman_balls_faced: int, mindset: str,
                    required_rpo: float | None, runs_needed: int | None) -> None:
    current = max(0, int(over_runs or 0))
    high = max(0, int(high_run_overs or 0))
    extreme = max(0, int(very_high_run_overs or 0))
    balls = max(0, int(batsman_balls_faced or 0))
    over = int(over_number or 0)

    repeat_scale = (0.75 ** high) * (0.45 ** extreme)
    if repeat_scale < 1.0:
        for key in (4, 6):
            if key in weights:
                weights[key] = max(0.0, float(weights[key])) * repeat_scale

    if current < 6:
        return
                                                    
    if runs_needed is not None and 0 < runs_needed <= 6:
        return
    if required_rpo is not None and required_rpo >= 22.0:
        return

    ceiling = _over_run_ceiling(over, balls, mindset, required_rpo)
    for shot in (3, 4, 6):
        if shot not in weights:
            continue
        z = (current + shot - ceiling) / OVER_RUN_SOFTNESS
        factor = 1.0 / (1.0 + math.exp(min(z, 30.0)))
        weights[shot] = max(0.0, float(weights[shot])) * max(OVER_RUN_MIN_SHOT_SCALE, factor)


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
                                                                            
                                                        
    desired_w = cap * total
    scale_non_w = (total - desired_w) / non_w_total
    for key in list(weights):
        if key != "W":
            weights[key] = max(0.0, float(weights[key])) * scale_non_w
    weights["W"] = desired_w


                                  
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


SECOND_INNINGS_BASE_RPO_CAP = 12.12
SECOND_INNINGS_REQUIRED_RPO_TRIGGER = 13.0
SECOND_INNINGS_RPO_EXTENSION = 2.0
SECOND_INNINGS_OVER_RUN_HOLD = 16
SECOND_INNINGS_EXTENSION_THROUGH_OVER = 15
SECOND_INNINGS_LAST_OVER = 20


def _effective_second_innings_rpo_cap(
    pitch: str,
    required_rpo: float,
    over_number: int,
    over_runs: int,
) -> float:
    """Return the active second-innings soft RPO ceiling.

    The normal pitch ceiling remains the baseline.  When the required RPO is
    above 13, the chase is in overs 1-15 or the final over, and the current
    over is still below 16 runs, the ceiling temporarily extends to required
    RPO + 2.0.  This helper is deliberately pure so every pitch condition uses
    exactly the same rule and boundary testing is easy.
    """
    base_cap = min(SECOND_INNINGS_BASE_RPO_CAP, pitch_target_rpo(pitch))
    required = max(0.0, float(required_rpo or 0.0))
    over = max(1, int(over_number or 1))
    current_over_runs = max(0, int(over_runs or 0))

    if required <= SECOND_INNINGS_REQUIRED_RPO_TRIGGER:
        return base_cap

    extension_window = over <= SECOND_INNINGS_EXTENSION_THROUGH_OVER or over >= SECOND_INNINGS_LAST_OVER
    if not extension_window or current_over_runs >= SECOND_INNINGS_OVER_RUN_HOLD:
        return base_cap

    return max(base_cap, required + SECOND_INNINGS_RPO_EXTENSION)


def _apply_pitch_score_environment(
    weights: dict,
    pitch: str,
    is_second_innings: bool = False,
    *,
    target: int | None = None,
    total_runs: int = 0,
    balls_remaining: int = 120,
    over_number: int = 0,
    over_runs: int = 0,
) -> None:
    """Gently pull the ball-outcome distribution toward the pitch's desired
    first-innings scoring environment without imposing a hard score cap.
    The current tactical/pitch/phase choices still dominate each ball.
    """
    target_rpo = pitch_target_rpo(pitch)
                                                                              
                                                                        
                                                                        
                                                                      
                                              
    if is_second_innings:
        target_rpo = min(SECOND_INNINGS_BASE_RPO_CAP, target_rpo)
        remaining_balls = max(0, int(balls_remaining or 0))
        if target is not None and remaining_balls > 0:
            runs_needed = max(0, int(target) - int(total_runs or 0))
            required_rpo = (runs_needed * 6.0) / remaining_balls
            target_rpo = _effective_second_innings_rpo_cap(
                pitch, required_rpo, over_number, over_runs
            )
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    expected = sum((k if isinstance(k, int) else (1.0 if k in {"WD", "NB", "LB", "BY"} else 0.0)) * max(0.0, float(v)) for k, v in weights.items()) / total
    gap = target_rpo - expected
                                                                          
                                                                            
                                                                      
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


                                                      
                                                                         
                                                                       
                                                                      
                                                                             
                                                                         
LEVEL_MIN = 1
LEVEL_MAX = 99
LEVEL_GAP_MAX_BUDGET = 0.10                                                        


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
                                                                     
        budget = min(LEVEL_GAP_MAX_BUDGET, 0.065 + 0.010 * (gap - 6))
    return max(0.0, min(LEVEL_GAP_MAX_BUDGET, budget))


                                    
                                                                            
                                                                           
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
                                                                           
                                                                         
    if confidence >= 70 and bowler_level > batter_level:
        _move_probability_mass(weights, "W", 1, min(0.0084, ((confidence - 65) / 2500) * 0.70))


def _apply_confidence_micro(weights: dict, confidence: float, balls_faced: int) -> None:
    """Soft confidence conversion; never applies a bowler-heavy penalty."""
    if not CONFIDENCE_ENGINE_ENABLED:
        return
    value = max(0.0, min(100.0, float(confidence or 0.0)))
    if value < 25:
        _apply(weights, {0: 1.012, "W": 1.006, 4: 0.994, 6: 0.990})
    elif value >= 80 and balls_faced >= 10:
        _apply(weights, {1: 1.009, 2: 1.009, 4: 1.017, 6: 1.013, "W": 0.9951})
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

                                                                              
    strength = 1.0 if is_lower else 0.45
                                                                
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

                                                                           
                                                                            
                                                                      
    level_gap_for_cap = max(0, int(diff or 0))
    if pitch in {"flat", "hard"}:
        base_cap = 0.050 if pitch_name == "flat" else 0.030
        gap_step = 0.0015
        max_cap = 0.050 if pitch_name == "flat" else 0.045
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

                                                                   
    if balls_faced >= 45:
        wicket_cap *= 0.895
    elif balls_faced >= 30:
        wicket_cap *= 0.93
    elif balls_faced >= 15:
        wicket_cap *= 0.965
    _redistribute_excess(weights, "W", wicket_cap, (0, 1, 2, 4, 6))

                                                                            
                                                               
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

                                                                              
    move_single = available * (requested_from_single / max(1e-12, available))
    move_boundary = available * (requested_from_boundary / max(1e-12, available))

    probs[1] = max(0.0, probs.get(1, 0.0) - move_single)
    if source_boundary > 0 and move_boundary > 0:
        for key in (4, 6):
            share = probs.get(key, 0.0) / source_boundary
            probs[key] = max(0.0, probs.get(key, 0.0) - move_boundary * share)

    probs[0] = probs.get(0, 0.0) + dot_add
    probs["W"] = probs.get("W", 0.0) + wicket_add

                                                                           
                                                                     
    new_total = sum(max(0.0, v) for v in probs.values())
    if new_total <= 0:
        return
    for key in list(weights):
        weights[key] = max(0.0, probs.get(key, 0.0) * total / new_total)

                                                                      
                                                                     
                                                                         
                              
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

                                                                          
                                                                        
                                                                             
                                         
                                                                    
 
                                                                          
                                                                           
                                                                             

FRESH_ULTRA_RISK: dict[str, dict[str, tuple[float, float]]] = {
                                                         
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

                                                                       
                                                                               
    floors = {0: 0.05, 1: 0.04, 2: 0.01, 4: 0.015, 6: 0.003, "W": 0.008, "WD": 0.001, "NB": 0.001}

    desired = {k: budget * share for k, share in source.items() if k in probs}
    requested_total = sum(desired.values())
    if requested_total <= 0:
        return

                                                                           
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

                                                                            
                                                                     
    target_total = sum(target.values())
    if target_total <= 0 or moved <= 0:
        return
    for key, share in target.items():
        if key in probs:
            probs[key] += moved * (share / target_total)

                                         
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
        base_cap, gap_step, max_cap = ((0.050, 0.0015, 0.050) if pitch_name == "flat" else (0.030, 0.0015, 0.045))
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


def _reduce_probability_into_rotation(weights: dict, source_key, reduction_fraction: float, targets: tuple = (1, 2)) -> None:
    total = sum(max(0.0, float(v)) for v in weights.values())
    source = max(0.0, float(weights.get(source_key, 0.0)))
    if total <= 0 or source <= 0:
        return
    reduction_fraction = max(0.0, min(1.0, float(reduction_fraction)))
    moved = source * reduction_fraction
    weights[source_key] = source - moved
    target_total = sum(max(0.0, float(weights.get(k, 0.0))) for k in targets)
    if target_total <= 0:
                                                                     
        weights[source_key] = source
        return
    for key in targets:
        current = max(0.0, float(weights.get(key, 0.0)))
        weights[key] = current + moved * (current / target_total)


def _increase_probability_relative(weights: dict, target_key, relative_increase: float) -> None:
    total = sum(max(0.0, float(v)) for v in weights.values())
    current = max(0.0, float(weights.get(target_key, 0.0)))
    if total <= 0 or current <= 0:
        return
    old_p = current / total
    desired_p = min(0.99, old_p * (1.0 + max(0.0, float(relative_increase))))
    delta = (desired_p - old_p) * total
    non_target = total - current
    if delta <= 0 or non_target <= 0:
        return
    scale = max(0.0, (non_target - delta) / non_target)
    for key in list(weights):
        if key == target_key:
            continue
        weights[key] = max(0.0, float(weights[key])) * scale
    weights[target_key] = current + delta


def _apply_boundary_tuning(weights: dict, *, pitch: str, over_number: int, batsman_balls_faced: int, confidence: float) -> None:
    """Apply the requested four/six boosts as a final relative tuning layer."""
    pitch_name = str(pitch or "even").strip().lower()
    if pitch_name != "flat":
        for key in (4, 6):
            if key in weights:
                weights[key] = max(0.0, float(weights[key])) * 1.05

                                                                           
                                                                                    
    if int(over_number or 0) >= 16 and int(batsman_balls_faced or 0) >= 16 and float(confidence or 0.0) >= 80.0:
        for key in (4, 6):
            if key in weights:
                weights[key] = max(0.0, float(weights[key])) * 1.05


def _apply_requested_probability_tuning(
    weights: dict,
    *,
    mindset: str,
    pitch: str,
    batsman_balls_faced: int,
    free_hit_next_ball: bool = False,
) -> None:
    """Final, isolated tuning requested for all three public match engines.

    • Defensive and rotate remain untouched.
    • Neutral/aggressive/ultra-aggressive reduce the established dot mass by
      37.5% only after the batter has faced more than five legal balls, then
      redistribute that exact mass into singles/doubles.
    • Wide/no-ball mass is reduced by 45% globally and returned to singles/
      doubles, preventing the existing high-extra issue.
    • Flat-pitch wicket exposure now uses the dedicated 5% final guardrail.
    • Boundary conversion receives a final 5% tuning layer without replacing
      the existing pitch/tactic/phase/confidence logic.
    • A no-ball's immediate free hit allows no ordinary dismissal (the odds
      are rebuilt in _free_hit_weights: only a rare run-out remains).
    """
    balls = max(0, int(batsman_balls_faced or 0))
    if mindset in {"neutral", "aggressive", "ultra_aggressive"} and balls > 5:
        _reduce_probability_into_rotation(weights, 0, 0.375, (1, 2))

    _reduce_probability_into_rotation(weights, "WD", 0.45, (1, 2))
    _reduce_probability_into_rotation(weights, "NB", 0.45, (1, 2))

    if free_hit_next_ball:
        _redistribute_excess(weights, "W", 0.0, (0, 1, 2, 3, 4, 6, "WD", "NB", "LB", "BY"))


                                                                             
                                                                         
 
                                                                            
                                                                          
                          
                                                                             
                                                                            
                                                                            
                                                                           
                              
                                                                           
                                  
                                                                             
                                                                          
                                                                            
                                                                       
                                                                             
EXTRAS_REALISM_ENABLED = True
WIDE_SCALE = 0.72                                                 
NO_BALL_SCALE = 0.50                                                  
NO_BALL_LEVEL_SLOPE = 0.008                                            
NO_BALL_LEVEL_BOUNDS = (0.80, 1.30)
NO_BALL_SPIN_FACTOR = 0.80                                

FREE_HIT_RUNOUT_P = 0.010                                                         
FREE_HIT_FOUR_BOOST = 1.40
FREE_HIT_SIX_BOOST = 1.60
FREE_HIT_NO_BALL_SCALE = 0.25                                                       
FREE_HIT_BOUNDARY_CEILING = 0.46                                               


def _apply_extras_realism(weights: dict, *, bowler_level: int,
                          bowler_style: str | None, bowler_role: str | None) -> None:
    """Scale wides / no-balls down and give the freed mass back, pro rata, to
    every other outcome (so the legal-ball odds do not move)."""
    if not EXTRAS_REALISM_ENABLED:
        return
    wd = max(0.0, float(weights.get("WD", 0.0)))
    nb = max(0.0, float(weights.get("NB", 0.0)))
    if wd <= 0.0 and nb <= 0.0:
        return
    lo, hi = NO_BALL_LEVEL_BOUNDS
    nb_scale = NO_BALL_SCALE * max(lo, min(hi, 1.0 + (70 - int(bowler_level or 0)) * NO_BALL_LEVEL_SLOPE))
    if bowler_family(bowler_style, bowler_role) == "spin":
        nb_scale *= NO_BALL_SPIN_FACTOR
    new_wd = wd * WIDE_SCALE
    new_nb = nb * nb_scale
    released = (wd - new_wd) + (nb - new_nb)
    weights["WD"], weights["NB"] = new_wd, new_nb
    others = [k for k in weights if k not in ("WD", "NB")]
    base = sum(max(0.0, float(weights[k])) for k in others)
    if base > 0.0 and released:
        factor = 1.0 + released / base
        for k in others:
            weights[k] = max(0.0, float(weights[k])) * factor


def _free_hit_weights(base: dict) -> dict:
    """The odds for the ball after a no-ball, built from what the SAME ball
    would have been without the free hit (`base`).

    * wicket  -> only a rare run-out (FREE_HIT_RUNOUT_P); the callers force the
                 dismissal kind to run-out
    * 4 / 6   -> boosted (the batter can take any risk)
    * no-ball -> much less likely again
    * dots / singles / twos / threes / wides / byes keep their relative odds.
    """
    total = sum(max(0.0, float(v)) for v in base.values())
    if total <= 0.0:
        return dict(base)
    p = {k: max(0.0, float(base.get(k, 0.0))) / total for k in OUTCOMES}
    out = dict(p)
    out["W"] = FREE_HIT_RUNOUT_P
    out["NB"] = p["NB"] * FREE_HIT_NO_BALL_SCALE
    b4 = p[4] * FREE_HIT_FOUR_BOOST
    b6 = p[6] * FREE_HIT_SIX_BOOST
    if b4 + b6 > FREE_HIT_BOUNDARY_CEILING:
        squeeze = FREE_HIT_BOUNDARY_CEILING / (b4 + b6)
        b4, b6 = b4 * squeeze, b6 * squeeze
    out[4], out[6] = b4, b6
    pinned = ("W", "NB", 4, 6)
    rest = [k for k in OUTCOMES if k not in pinned]
    rest_mass = sum(p[k] for k in rest)
    room = 1.0 - sum(out[k] for k in pinned)
    if rest_mass > 0.0 and room > 0.0:
        scale = room / rest_mass
        for k in rest:
            out[k] = p[k] * scale
    return {k: out[k] * total for k in OUTCOMES}


def _set_probability_floor(weights: dict, target_key, target_probability: float) -> None:
    """Raise one outcome to an exact minimum share of total probability mass."""
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    current = max(0.0, float(weights.get(target_key, 0.0)))
    current_p = current / total
    desired_p = max(0.0, min(0.99, float(target_probability)))
    if current_p >= desired_p:
        return
    delta = (desired_p - current_p) * total
    non_target = total - current
    if non_target <= 0:
        return
    for key in list(weights):
        if key == target_key:
            continue
        value = max(0.0, float(weights.get(key, 0.0)))
        weights[key] = value * max(0.0, (non_target - delta) / non_target)
    weights[target_key] = current + delta


def _increase_single_probability_relative(weights: dict, increase: float = 0.12) -> None:
    """Increase the normalized probability of outcome `1` by a relative 12%.

    The adjustment is isolated to outcome `1` and preserves total probability
    mass by scaling every other outcome proportionally. Existing tactical,
    pitch, phase, level, confidence, realism and upgrade rules remain intact.
    """
    factor = max(0.0, 1.0 + float(increase))
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    p1 = max(0.0, float(weights.get(1, 0.0))) / total
    if p1 <= 0.0 or p1 >= 1.0:
        return
    new_p1 = min(1.0, p1 * factor)
    other_scale = (1.0 - new_p1) / max(1e-12, 1.0 - p1)
    for key in list(weights):
        if key == 1:
            weights[key] = new_p1 * total
        else:
            weights[key] = max(0.0, float(weights[key])) * other_scale


                                                                             
                                       
                                                                           
                                        
                                                                             
                                                                       
 
              
                                                                            
                                                                              
                                                                            
                                                                         
                                                         
                                                                        
                                                                           
                                                                           
                                                                          
                                                             
 
              
                                                                             
                                                                          
                                                                              
                                                                         
                                                                          
                                                                             
                                                       
                                                                             
                                                                      
                                                                            
                                                   
                                                                        
                                                                              
                                                                           
                                                                            
                                        
                                                                           
                                    
                                                                             
                                                            
                                                                            
                                                                             
                                                                
                                                                              
                                                                           
                                                                       
                                                                           
                                                                              
                                                                              
                                                                            
                                                                         
                                                                         
                                                                       
                                                                           
                                                                           
                                                                          
                                                              
REALISM_LAYER_ENABLED = True

_REALISM_GROUP = (2, 3, 4, 6, "W", "WD", "NB", "LB", "BY")

                                                                            
                                                                       
                           
MINDSET_PITCH_SENSITIVITY = {
    "defensive": 0.55, "rotate": 0.70, "neutral": 1.00,
    "aggressive": 1.20, "ultra_aggressive": 1.35,
}

                                                                    
                                                                    
                                                                          
                                                                   
                                                              
                                                                           
                                                                           
                                                                           
                                                                         
                                                                         
PITCH_REALISM: dict = {
    "green": {"W": 0.985, 4: 0.250, 6: 0.238},
    "dusty": {"W": 0.994, 4: 0.200, 6: 0.180},
    "dry": {"W": 1.033, 4: 0.325, 6: 0.296},
    "hard": {"W": 1.924, 4: 3.382, 6: 3.382},
    "flat": {"W": 0.733, 4: 6.005, 6: 6.755},
    "bouncy": {"W": 0.986, 4: 0.449, 6: 0.477},
    "slow": {"W": 1.050, 4: 0.108, 6: 0.089},
    "even": {"W": 1.364, 4: 0.726, 6: 0.726},
}

                                                               
FAMILY_PITCH_W: dict = {
    "green": (1.10, 0.92), "bouncy": (1.10, 0.95), "hard": (1.05, 0.97),
    "flat": (1.00, 1.00), "even": (1.00, 1.02),
    "dry": (0.94, 1.12), "dusty": (0.92, 1.16), "slow": (0.95, 1.10),
}

                                                                         
                                                                           
                                                                          
                                            
PITCH_DYNAMICS: dict = {
    "green": (0.20, -0.14, 0.05, 0.06),
    "bouncy": (0.14, -0.10, 0.05, 0.02),
    "hard": (0.08, -0.05, 0.06, -0.02),
    "flat": (0.04, -0.03, 0.05, -0.03),
    "even": (0.06, -0.04, 0.08, -0.02),
    "dry": (0.05, -0.07, 0.20, -0.06),
    "dusty": (0.04, -0.09, 0.28, -0.08),
    "slow": (0.04, -0.06, 0.20, -0.08),
}

                                                                                
                                                                             
                                                    
                                                                              
                                            
                                                                      
OVER_PROFILE: dict = {
    "W": [0.954, 1.030, 1.032, 0.979, 0.922, 0.898,
          1.098, 1.069, 1.010, 0.960, 0.947, 0.959, 0.980, 1.045, 1.124,
          0.859, 0.937, 1.020, 1.071, 1.106],
    4: [0.209, 0.248, 0.298, 0.364, 0.446, 0.500,
        0.092, 0.081, 0.080, 0.110, 0.192, 0.370, 0.711, 1.199, 1.597,
        0.748, 1.320, 2.525, 3.983, 4.926],
    6: [0.195, 0.232, 0.278, 0.341, 0.418, 0.469,
        0.084, 0.074, 0.074, 0.103, 0.181, 0.351, 0.674, 1.137, 1.514,
        0.769, 1.357, 2.595, 4.094, 5.063],
}

                                                                           
                                                                     
WICKET_FLOOR_BY_MINDSET = {
    "defensive": 0.012, "rotate": 0.015, "neutral": 0.022,
    "aggressive": 0.032, "ultra_aggressive": 0.048,
}
                                                                              
                                                                            
                                                                          
MINDSET_W_RISK = {
    "defensive": 1.00, "rotate": 1.00, "neutral": 1.00,
    "aggressive": 1.10, "ultra_aggressive": 1.10,
}
                                                                      
                                                                         
                                                                               
                                                                             
                                                                              
WICKET_FINE_TUNE = 0.93
                                                                            
                                                                               
TWOS_ABSORB_MAX = 1.6
                                                                                
                                                                             
                                                                  
TWOS_FLOOR_BY_MINDSET = {
    "defensive": 0.45, "rotate": 0.45, "neutral": 0.45,
    "aggressive": 0.38, "ultra_aggressive": 0.22,
}
                                                                                 
                                                                            
MINDSET_BND_SCALE = {
    "defensive": 1.00, "rotate": 1.00, "neutral": 1.00,
    "aggressive": 0.85, "ultra_aggressive": 1.25,
}
PITCH_WICKET_FLOOR_SCALE = {
    "green": 1.15, "bouncy": 1.12, "dusty": 1.08, "dry": 1.05,
    "slow": 1.05, "even": 1.00, "hard": 0.95, "flat": 0.92,
}
PHASE_WICKET_FLOOR_SCALE = {"overs_1_6": 1.00, "overs_7_15": 0.90, "overs_16_20": 1.20}

                                                                            
                                                       
BOUNDARY_FLOOR_BY_MINDSET = {
    "defensive": (0.008, 0.0005), "rotate": (0.016, 0.002), "neutral": (0.034, 0.007),
    "aggressive": (0.075, 0.022), "ultra_aggressive": (0.120, 0.055),
}
PITCH_BOUNDARY_FLOOR_SCALE = {
    "flat": 1.30, "hard": 1.15, "even": 1.00, "bouncy": 0.95,
    "dry": 0.90, "green": 0.88, "dusty": 0.85, "slow": 0.80,
}
PHASE_BOUNDARY_FLOOR_SCALE = {"overs_1_6": 1.05, "overs_7_15": 0.92, "overs_16_20": 1.30}

                                                                         
                                                                              
                                    
COLLAPSE_PHASE_SCALE = {"overs_1_6": 0.60, "overs_7_15": 0.85, "overs_16_20": 1.00}
COLLAPSE_W_PER_WICKET = 0.065
COLLAPSE_FOUR_PER_WICKET = 0.040
COLLAPSE_SIX_PER_WICKET = 0.050
COLLAPSE_WINDOW_BALLS = 12

                                                                                
                                                                                 
                                                                               
                                                                                 
                                                                           
                                                                               
                                                                               
                                                                              
OVER_CLUSTER_W = {1: 0.26, 2: 0.18}                                              
HAT_TRICK_GUARD = 0.60                                                                   
CLUSTER_TAIL_EASE = 0.50                                                                         
CLUSTER_PHASE_SCALE = {"overs_1_6": 1.00, "overs_7_15": 1.00, "overs_16_20": 1.00}


def _over_cluster_factor(wickets_this_over: int, balls_since_wicket: int | None,
                         phase: str, tail_s: float) -> float:
    """Multiplier on the wicket chance once the over already has wicket(s)."""
    try:
        k = int(wickets_this_over or 0)
    except (TypeError, ValueError):
        return 1.0
    if k <= 0:
        return 1.0
    if k >= 3:
        return 0.0
    f = OVER_CLUSTER_W[k] * CLUSTER_PHASE_SCALE.get(phase, 1.0)
    if tail_s > 0.0:
        f = min(1.0, f) ** (1.0 - CLUSTER_TAIL_EASE * min(1.0, tail_s))
    if k >= 2 and balls_since_wicket is not None and balls_since_wicket <= 0:
        f *= HAT_TRICK_GUARD
    return max(0.0, min(1.0, f))


def _ball_in_over(over_number: int, balls_remaining: int) -> int | None:
    """Legal balls already bowled in the current over (0-5), or None when the
    caller's over / balls_remaining do not describe a 20-over innings."""
    try:
        done = 120 - int(balls_remaining) - (int(over_number) - 1) * 6
    except (TypeError, ValueError):
        return None
    return done if 0 <= done <= 5 else None


def _required_rpo(target: int | None, total_runs: int, balls_remaining: int) -> float | None:
    if target is None:
        return None
    balls = int(balls_remaining or 0)
    if balls <= 0:
        return None
    need = max(0, int(target) - int(total_runs or 0))
    return need * 6.0 / balls


def _infer_recent_wickets(wickets_this_over: int, batting_position: int,
                          wickets_in_hand: int, balls_faced: int) -> int:
    """Fallback when the caller does not pass `recent_wickets`: wickets in
    this over, plus one if the striker is the newest batter and still fresh."""
    recent = max(0, int(wickets_this_over or 0))
    fallen = 10 - int(wickets_in_hand if wickets_in_hand is not None else 10)
    if fallen >= 1 and int(batting_position or 0) == fallen + 2 and int(balls_faced or 0) <= 8:
        recent = max(recent, 1)
    return recent


def _pitch_dynamics(pitch: str, family: str, over: int) -> dict:
    """New-ball bite + surface wear.  Returns multipliers for W / 4 / 6."""
    cfg = PITCH_DYNAMICS.get(pitch)
    if not cfg:
        return {}
    fresh_pace_w, pace_wear_w, spin_wear_w, bnd_wear = cfg
    o = max(1, min(20, int(over)))
    centred_wear = o / 20.0 - 0.5
    fresh = max(0.0, 8 - o) / 7.0
    if family == "pace":
        w = 1.0 + fresh_pace_w * fresh + pace_wear_w * centred_wear * 2.0
    else:
        w = 1.0 + spin_wear_w * centred_wear * 2.0
    b = 1.0 + bnd_wear * centred_wear * 2.0
    return {"W": max(0.5, w), 4: max(0.6, b), 6: max(0.6, b)}


def _settle_wicket_factor(balls_faced: int) -> float:
    balls = max(0, int(balls_faced or 0))
    if balls <= 2:
        return 1.18
    if balls <= 5:
        return 1.10
    if balls <= 10:
        return 1.04
    if balls <= 20:
        return 1.00
    if balls <= 35:
        return 0.95
    return 0.91


def _momentum_factors(ball_in_over: int | None, over_runs: int) -> tuple[float, float, float]:
    """(W, 4, 6) multipliers from the shape of the over so far."""
    if ball_in_over is None:
        return 1.0, 1.0, 1.0
    runs = max(0, int(over_runs or 0))
    if ball_in_over >= 3 and runs <= 2:
                                                                              
        return 1.10, 1.12, 1.16
    if ball_in_over >= 2 and runs >= 10:
                                      
        return 1.08, 1.0, 1.0
    return 1.0, 1.0, 1.0


def _collapse_factors(recent_wickets: int, balls_since_wicket: int | None,
                      phase: str) -> tuple[float, float, float]:
    """(W, 4, 6) multipliers for back-to-back wickets (slight, phase scaled)."""
    k = min(3, max(0, int(recent_wickets or 0)))
    if k <= 0:
        return 1.0, 1.0, 1.0
    scale = COLLAPSE_PHASE_SCALE.get(phase, 0.85)
    w = 1.0 + COLLAPSE_W_PER_WICKET * k * scale
    b4 = 1.0 - COLLAPSE_FOUR_PER_WICKET * k * scale
    b6 = 1.0 - COLLAPSE_SIX_PER_WICKET * k * scale
    if balls_since_wicket is not None and balls_since_wicket <= 1:
        w *= 1.05                                               
    return w, b4, b6


                                                                              
                                                                           
                                                                         
                                                                       
                                                                       
                                                                     
                                                                             
                                                      
TAIL_INTENT_W = {
    "defensive": 0.78, "rotate": 0.80, "neutral": 0.92,
    "aggressive": 1.70, "ultra_aggressive": 1.95,
}
TAIL_INTENT_BND = {                                                 
    "defensive": 0.60, "rotate": 0.80, "neutral": 1.00,
    "aggressive": 1.35, "ultra_aggressive": 1.60,
}
TAIL_W_CAP = {                                                            
    "aggressive": 0.19, "ultra_aggressive": 0.22,
}
TAIL_BND_KEEP = {                                                                
    "aggressive": 0.50, "ultra_aggressive": 0.55,
}
TAIL_SINGLE_TAKE = {                                                        
    "aggressive": 0.22, "ultra_aggressive": 0.28,
}
TAIL_SINGLE_GIVE = {                                                           
    "rotate": 0.04, "neutral": 0.04,
}
TAIL_BAND_STRENGTH = {
    "soft_tail": 0.25, "lower_order": 0.55, "tailender": 0.85,
    "deep_tail": 1.00, "extreme_tail": 1.10,
}


def _tail_strength(batter_level: int) -> float:
    band = _tailender_band(batter_level)
    return TAIL_BAND_STRENGTH.get(band, 0.0) if band else 0.0


                                                                               
                                                                               
                                                                           
                                                                      
                                                              
RESOURCE_EXPECTED_LOST = 6.2                                                  
RESOURCE_CURVE = 1.3
RESOURCE_SCALE = 1.6
RESOURCE_RAMP_START, RESOURCE_RAMP_LEN = 2, 6                           
LAUNCH_W_UP, LAUNCH_W_DOWN = 1.30, 0.40
LAUNCH_B_UP, LAUNCH_B_DOWN = 0.20, 0.14


def _resource_surplus(balls_remaining, wickets_in_hand) -> float | None:
    try:
        left = int(balls_remaining)
        hand = int(wickets_in_hand)
    except (TypeError, ValueError):
        return None
    if not (0 <= left <= 120) or not (1 <= hand <= 10):
        return None
    expected_lost = RESOURCE_EXPECTED_LOST * ((120 - left) / 120.0) ** RESOURCE_CURVE
    return hand - (10.0 - expected_lost)


                                                                                 
                                                                           
                                                                           
DROUGHT_W = 0.80
DROUGHT_START, DROUGHT_LEN = 12, 30


def _drought_factor(balls_since_wicket, balls_remaining, tail_s: float,
                    wickets_in_hand=None) -> float:
    if DROUGHT_W <= 0.0 or tail_s > 0.0:
        return 1.0
    try:
        if balls_since_wicket is None:
                                                                               
                                                                            
            if wickets_in_hand is not None and int(wickets_in_hand) < 10:
                return 1.0
            since = 120 - int(balls_remaining)
        else:
            since = int(balls_since_wicket)
    except (TypeError, ValueError):
        return 1.0
    x = max(0.0, min(1.0, (since - DROUGHT_START) / float(DROUGHT_LEN)))
    return 1.0 + DROUGHT_W * x * x * (3.0 - 2.0 * x)


def _launch_factors(over: int, surplus: float | None, tail_s: float = 0.0) -> tuple[float, float]:
    """(W, boundary) multipliers from the wickets-in-hand surplus."""
    if surplus is None:
        return 1.0, 1.0
    phase_weight = max(0.0, min(1.0, (int(over) - RESOURCE_RAMP_START) / float(RESOURCE_RAMP_LEN)))
    if phase_weight <= 0.0:
        return 1.0, 1.0
    x = math.tanh(surplus / RESOURCE_SCALE)
    if x >= 0.0:
        return 1.0 + phase_weight * LAUNCH_W_UP * x, 1.0 + phase_weight * LAUNCH_B_UP * x
    keep = 1.0 - min(1.0, tail_s)
    return (max(0.40, 1.0 + phase_weight * LAUNCH_W_DOWN * x * keep),
            max(0.35, 1.0 + phase_weight * LAUNCH_B_DOWN * x * keep))


def _apply_realism_layer(
    weights: dict,
    *,
    pitch: str,
    mindset: str,
    over_number: int,
    batter_level: int,
    bowler_level: int,
    batsman_balls_faced: int,
    bowler_style: str | None,
    bowler_role: str | None,
    ball_in_over: int | None,
    over_runs: int,
    wickets_this_over: int,
    recent_wickets: int,
    balls_since_wicket: int | None,
    free_hit_next_ball: bool,
    balls_remaining: int | None = None,
    wickets_in_hand: int | None = None,
) -> None:
    if not REALISM_LAYER_ENABLED:
        return
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return
    p = {k: max(0.0, float(weights.get(k, 0.0))) / total for k in OUTCOMES}
    mass = 1.0 - p[0] - p[1]
    if mass <= 1e-9:
        return

    pitch_name = str(pitch or "even").strip().lower()
    balls = max(0, int(batsman_balls_faced or 0))
    over = max(1, int(over_number or 1))
    oi = min(19, over - 1)
    family = bowler_family(bowler_style, bowler_role)
    phase = phase_key(over)
    sens = MINDSET_PITCH_SENSITIVITY.get(mindset, 1.0)

    static = PITCH_REALISM.get(pitch_name, {})
    dyn = _pitch_dynamics(pitch_name, family, over)
    fam_w = FAMILY_PITCH_W.get(pitch_name, (1.0, 1.0))[0 if family == "pace" else 1]
                                                                             
    m_w = (static.get("W", 1.0) * dyn.get("W", 1.0) * fam_w * OVER_PROFILE["W"][oi]) ** sens
    m_4 = (static.get(4, 1.0) * dyn.get(4, 1.0) * OVER_PROFILE[4][oi]) ** sens
    m_6 = (static.get(6, 1.0) * dyn.get(6, 1.0) * OVER_PROFILE[6][oi]) ** sens

    mom_w, mom_4, mom_6 = _momentum_factors(ball_in_over, over_runs)
    col_w, col_4, col_6 = _collapse_factors(recent_wickets, balls_since_wicket, phase)
    bnd_scale = MINDSET_BND_SCALE.get(mindset, 1.0)
    surplus = _resource_surplus(balls_remaining, wickets_in_hand)
    tail_s = _tail_strength(batter_level)
    lau_w, lau_b = _launch_factors(over, surplus, tail_s)
    m_w *= (_settle_wicket_factor(balls) * mom_w * col_w * lau_w
            * _drought_factor(balls_since_wicket, balls_remaining, tail_s, wickets_in_hand))
    m_4 *= mom_4 * col_4 * bnd_scale * lau_b
    m_6 *= mom_6 * col_6 * bnd_scale * lau_b

    t_w = 1.0
    single_shift = 0.0
    if tail_s > 0.0:
        t_w = 1.0 + (TAIL_INTENT_W.get(mindset, 1.0) - 1.0) * tail_s
        t_b = 1.0 + (TAIL_INTENT_BND.get(mindset, 1.0) - 1.0) * tail_s
        single_shift = (TAIL_SINGLE_GIVE.get(mindset, 0.0) - TAIL_SINGLE_TAKE.get(mindset, 0.0)) * tail_s
                                                                              
                                               
        m_4 = (m_4 ** (1.0 - 0.6 * tail_s)) * t_b
        m_6 = (m_6 ** (1.0 - 0.6 * tail_s)) * t_b

    new = {k: p[k] for k in _REALISM_GROUP}
    tail_floor4 = tail_floor6 = 0.0
    tail_w_cap = 0.30
    if tail_s > 0.0 and mindset in TAIL_BND_KEEP:
        keep = TAIL_BND_KEEP[mindset] * min(1.0, tail_s)
        tail_floor4, tail_floor6 = keep * p[4], keep * p[6]
        tail_w_cap = 0.30 - (0.30 - TAIL_W_CAP[mindset]) * min(1.0, tail_s / 0.85)
    if single_shift:
                                                                               
        p1 = p[1] * (1.0 + single_shift)
        mass = 1.0 - p[0] - p1
        weights[1] = p1 * total

                                                                             
                                                                
    cluster = _over_cluster_factor(wickets_this_over, balls_since_wicket, phase, tail_s)
    if p["W"] > 0.0 and not free_hit_next_ball:
        wk = p["W"] * m_w * MINDSET_W_RISK.get(mindset, 1.0) * t_w * cluster
        gap = int(batter_level or 0) - int(bowler_level or 0)
        level_scale = 0.72 if gap >= 30 else (0.85 if gap >= 15 else (1.12 if gap <= -15 else 1.0))
        floor = (WICKET_FLOOR_BY_MINDSET.get(mindset, 0.034)
                 * PITCH_WICKET_FLOOR_SCALE.get(pitch_name, 1.0)
                 * PHASE_WICKET_FLOOR_SCALE.get(phase, 1.0) * level_scale * cluster)
        new["W"] = min(max(wk, floor), tail_w_cap) * WICKET_FINE_TUNE
        if tail_floor4 or tail_floor6:
                                                                                  
            room_w = (mass - (new["WD"] + new["NB"] + new["LB"] + new["BY"])
                      - TWOS_FLOOR_BY_MINDSET.get(mindset, 0.45) * p[2] - tail_floor4 - tail_floor6)
            new["W"] = min(new["W"], max(0.0, room_w))
    if pitch_name == "flat" and not free_hit_next_ball:
                                                                              
                                                                                
        new["W"] = max(new["W"], 0.05 * cluster)

                                                                             
    set_batter = balls >= 6 and int(batter_level or 0) >= 52
    floor4 = floor6 = 0.0
    if set_batter:
        f4, f6 = BOUNDARY_FLOOR_BY_MINDSET.get(mindset, (0.034, 0.007))
        scale = (PITCH_BOUNDARY_FLOOR_SCALE.get(pitch_name, 1.0)
                 * PHASE_BOUNDARY_FLOOR_SCALE.get(phase, 1.0)
                 * (0.8 + 0.4 * max(0.0, min(1.0, (int(batter_level or 0) - 40) / 50.0))))
        floor4, floor6 = f4 * scale, f6 * scale
    new[4] = max(p[4] * m_4, floor4, tail_floor4)
    new[6] = max(p[6] * m_6, floor6, tail_floor6)

                                                                               
                                                                        
                                                                               
                                                       
    fixed = new["WD"] + new["NB"] + new["LB"] + new["BY"]
    flex_keys = (3, 4, 6)
    flex = sum(new[k] for k in flex_keys)
    target2 = mass - fixed - new["W"] - flex
    lo2, hi2 = TWOS_FLOOR_BY_MINDSET.get(mindset, 0.45) * p[2], TWOS_ABSORB_MAX * p[2]
    if target2 < lo2:
        room = mass - fixed - new["W"] - lo2
        if room >= 0.0:
            scale = (room / flex) if flex > 0 else 0.0
            for k in flex_keys:
                new[k] *= scale
        else:
                                                                                 
            for k in flex_keys:
                new[k] = 0.0
            new["W"] = max(0.0, mass - fixed - lo2)
        new[2] = max(0.0, mass - fixed - new["W"] - sum(new[k] for k in flex_keys))
    elif target2 > hi2 and flex > 0:
        spare = target2 - hi2
        for k in flex_keys:
            new[k] *= 1.0 + spare / flex
        new[2] = hi2
    else:
        new[2] = max(0.0, target2)

    for k in _REALISM_GROUP:
        weights[k] = new[k] * total


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
    free_hit_next_ball: bool = False,
    batting_upgrade_context: dict | None = None,
    bowling_upgrade_context: dict | None = None,
    recent_wickets: int | None = None,
    balls_since_wicket: int | None = None,
) -> dict:
    """Runs the full 6-layer algorithm and returns final weights, ready
    for a weighted-random pick."""
    if free_hit_next_ball:
                                                                       
                                                                         
        call_args = dict(locals())
        call_args["free_hit_next_ball"] = False
        return _free_hit_weights(resolve_weights(**call_args))

    mindset = batter_mindset if batter_mindset in BASE_WEIGHTS_BY_MINDSET else "neutral"
    tactic = bowler_tactic if bowler_tactic in BOWLER_TACTICS else "swinging"
    tactic_profile = _tactic_archetype(tactic)

                                                                
    ball_in_over = _ball_in_over(over_number, balls_remaining)
    required_rpo = _required_rpo(target, total_runs, balls_remaining)
    runs_needed = None if target is None else max(0, int(target) - int(total_runs or 0))
    if recent_wickets is None:
        recent_wickets = _infer_recent_wickets(
            wickets_this_over, batting_position, wickets_in_hand, batsman_balls_faced,
        )

    weights = dict(BASE_WEIGHTS_BY_MINDSET[mindset])

    tactical = TACTICAL_MODIFIERS.get((tactic_profile, mindset)) or TACTICAL_MODIFIERS[("swinging", "neutral")]
    _apply(weights, tactical)

    _apply(weights, PITCH_MATRIX.get(pitch, {}))
    _apply_pitch_hitting_dampening(weights, pitch)
    _apply_pitch_score_environment(
        weights,
        pitch,
        is_second_innings=(target is not None),
        target=target,
        total_runs=total_runs,
        balls_remaining=balls_remaining,
        over_number=over_number,
        over_runs=over_runs,
    )
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

                                                                        
                                                                          
    _apply_fresh_ultra_micro(
        weights, pitch, over_number, batsman_balls_faced, mindset,
    )
    if mindset == "defensive":
        _apply_defensive_adaptation_micro(
            weights, batter_level, bowler_level, batsman_balls_faced, confidence,
        )
    _apply_tailender_micro(weights, batter_level)
    _apply_final_wicket_cap(
        weights, pitch, batter_level, bowler_level, batsman_balls_faced,
        int(wickets_this_over or 0),
    )

                                                                          
                                                                          
                                                                          
                                                                         
    _apply_requested_probability_tuning(
        weights,
        mindset=mindset,
        pitch=pitch,
        batsman_balls_faced=batsman_balls_faced,
        free_hit_next_ball=free_hit_next_ball,
    )
                                                                       
    _apply_extras_realism(
        weights, bowler_level=bowler_level,
        bowler_style=bowler_style, bowler_role=bowler_role,
    )

                                                                       
                                                                         
                                                                          
    from services.player_upgrades import apply_upgrade_layer, bowler_family, phase_for_over
    weights = apply_upgrade_layer(
        weights,
        batting_upgrade=batting_upgrade_context,
        bowling_upgrade=bowling_upgrade_context,
        phase=phase_for_over(over_number),
        tactic=tactic,
        mindset=mindset,
        pitch=pitch,
        bowler_family_name=bowler_family(bowler_role, bowler_style),
        batsman_balls_faced=batsman_balls_faced,
        target=target,
        total_runs=total_runs,
        balls_remaining=balls_remaining,
        confidence=confidence,
        batter_role=batter_role,
        bowler_role=bowler_role,
    )
                                                                            
                                                                         
                                                                           
    if batting_upgrade_context or bowling_upgrade_context:
        _apply_final_wicket_cap(
            weights, pitch, batter_level, bowler_level, batsman_balls_faced,
            int(wickets_this_over or 0),
        )

                                     
    _apply_boundary_tuning(
        weights,
        pitch=pitch,
        over_number=over_number,
        batsman_balls_faced=batsman_balls_faced,
        confidence=confidence,
    )

                                                                         
                                                                           
    _increase_single_probability_relative(weights, 0.12)

                                                                           
                                                                           
    for key in (4, 6):
        if key in weights:
            weights[key] = max(0.0, float(weights[key])) * 0.97

                                                                         
                                                                            
                               
                                                                         
                                                    
    if str(pitch or "even").strip().lower() == "flat" and not free_hit_next_ball:
        _set_probability_floor(weights, "W", 0.05)

    weights = {key: max(0.0, value) for key, value in weights.items()}

                                                                         
                                                                             
    _apply_realism_layer(
        weights,
        pitch=pitch, mindset=mindset, over_number=over_number,
        batter_level=batter_level, bowler_level=bowler_level,
        batsman_balls_faced=batsman_balls_faced,
        bowler_style=bowler_style, bowler_role=bowler_role,
        ball_in_over=ball_in_over, over_runs=over_runs,
        wickets_this_over=int(wickets_this_over or 0),
        recent_wickets=recent_wickets, balls_since_wicket=balls_since_wicket,
        free_hit_next_ball=free_hit_next_ball,
        balls_remaining=balls_remaining, wickets_in_hand=wickets_in_hand,
    )

                                                                          
                                                                              
    _apply_over_run_rarity(
        weights, over_runs, high_run_overs, very_high_run_overs, over_number,
        batsman_balls_faced, mindset=mindset, required_rpo=required_rpo,
        runs_needed=runs_needed,
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
             high_run_overs: int = 0, very_high_run_overs: int = 0,
             free_hit_next_ball: bool = False,
             batting_upgrade_context: dict | None = None,
             bowling_upgrade_context: dict | None = None,
             recent_wickets: int | None = None,
             balls_since_wicket: int | None = None):
    """Returns one sampled outcome code from OUTCOMES."""
    weights = resolve_weights(
        bowler_tactic, batter_mindset, pitch, over_number, batter_level, bowler_level, confidence,
        batsman_balls_faced=batsman_balls_faced,
        wickets_this_over=wickets_this_over,
        bowler_style=bowler_style, bowler_role=bowler_role, batter_role=batter_role,
        target=target, total_runs=total_runs, balls_remaining=balls_remaining,
        wickets_in_hand=wickets_in_hand, batting_position=batting_position,
        over_runs=over_runs, high_run_overs=high_run_overs, very_high_run_overs=very_high_run_overs,
        free_hit_next_ball=free_hit_next_ball,
        batting_upgrade_context=batting_upgrade_context,
        bowling_upgrade_context=bowling_upgrade_context,
        recent_wickets=recent_wickets, balls_since_wicket=balls_since_wicket,
    )
    values = [weights.get(key, 0.0) for key in OUTCOMES]
    return random.choices(OUTCOMES, weights=values, k=1)[0]


                                                                             
                                                              
 
                                                                          
                                                                            
                                                                    
                                                                            
                      
 
                                                   
                                                                    
                                                                          
                                                                            
                                                       
                                                                             
WICKET_KIND_WEIGHTS_SPIN = {"caught": 42.0, "bowled": 26.0, "lbw": 16.0, "stumped": 16.0}
WICKET_KIND_WEIGHTS_PACE = {"caught": 58.0, "bowled": 26.0, "lbw": 16.0}
RUN_OUT_CHANCE = 0.015                                                          
MAX_RUN_OUTS_PER_INNINGS = 1
CAUGHT_AND_BOWLED_CHANCE = 0.08                                                     
RUN_OUT_RELAY_CHANCE = 0.45                                                                         

                                                                        
                                                                            
                                                                          
                                                            
_KIND_PITCH_TILT = {
    "green": {"bowled": 1.10, "lbw": 1.10, "caught": 1.02},                                       
    "bouncy": {"caught": 1.15, "bowled": 0.85, "lbw": 0.80},                                          
    "hard": {"caught": 1.08, "bowled": 0.96},
    "flat": {"caught": 1.12, "bowled": 0.88, "lbw": 0.90, "stumped": 0.85},                                        
    "dusty": {"bowled": 1.15, "lbw": 1.15, "stumped": 1.20, "caught": 0.90},                      
    "dry": {"bowled": 1.10, "lbw": 1.12, "stumped": 1.12, "caught": 0.93},
    "slow": {"bowled": 1.08, "lbw": 1.08, "stumped": 1.10, "caught": 0.95},
}
_KIND_PHASE_TILT = {
    "overs_1_6": {"caught": 1.04, "bowled": 1.06, "lbw": 1.05},                                  
    "overs_7_15": {"lbw": 1.06, "stumped": 1.10, "caught": 0.96},                      
    "overs_16_20": {"caught": 1.16, "bowled": 1.10, "lbw": 0.82, "stumped": 0.80},                              
}
_KIND_MINDSET_TILT = {
    "defensive": {"bowled": 1.06, "lbw": 1.18, "caught": 0.82},
    "rotate": {"lbw": 1.10, "caught": 0.92},
    "aggressive": {"caught": 1.10, "stumped": 1.08, "lbw": 0.92},
    "ultra_aggressive": {"caught": 1.18, "bowled": 1.06, "lbw": 0.85, "stumped": 1.12},
}
_KIND_TACTIC_TILT = {
    "swinging": {"bowled": 1.12, "lbw": 1.10},
    "pace_up": {"caught": 1.05, "bowled": 1.05},
    "back_of_length": {"caught": 1.12, "lbw": 0.80, "bowled": 0.85},
    "variation": {"bowled": 1.15, "caught": 1.05},                                         
    "defensive": {"lbw": 1.10},
    "off_break": {"bowled": 1.05},
    "doosra": {"caught": 1.12, "stumped": 1.10},
    "arm_ball": {"lbw": 1.25, "bowled": 1.10},
    "carrom_ball": {"stumped": 1.08, "caught": 1.05},
    "top_spin": {"caught": 1.12},
    "leg_breaker": {"stumped": 1.10, "bowled": 1.05},
    "top_spinner": {"caught": 1.12},
    "slider": {"lbw": 1.25, "bowled": 1.12},
    "flipper": {"lbw": 1.25, "bowled": 1.15},
    "googly_ball": {"bowled": 1.15, "stumped": 1.12},
}


def wicket_kind_credits_bowler(kind: str | None) -> bool:
    """Run-outs are not a bowler's wicket; every other mode is."""
    return str(kind or "").strip().lower() != "run_out"


def pick_wicket_kind(bowler_style: str | None = None, bowler_role: str | None = None,
                     run_outs_so_far: int = 0, keeper_available: bool = True, *,
                     pitch: str | None = None, over_number: int | None = None,
                     tactic: str | None = None, mindset: str | None = None) -> str:
    """Pick how a wicket fell: caught / bowled / lbw / stumped / run_out.

    The optional pitch / over / tactic / mindset keywords tilt the mix toward
    what really happens in those conditions (stumpings on turners, bowled on
    green seamers, caught on flat decks and at the death ...).  Callers that
    do not pass them get the plain spin / pace mix exactly as before.
    """
    phase = phase_key(over_number) if over_number else None
    run_out_chance = RUN_OUT_CHANCE * (1.5 if phase == "overs_16_20" else 1.0)
    if run_outs_so_far < MAX_RUN_OUTS_PER_INNINGS and random.random() < run_out_chance:
        return "run_out"
    spin = bowler_family(bowler_style, bowler_role) == "spin"
    weights = dict(WICKET_KIND_WEIGHTS_SPIN if spin else WICKET_KIND_WEIGHTS_PACE)
    if not keeper_available:
        weights.pop("stumped", None)
    tilts = (
        _KIND_PITCH_TILT.get(str(pitch or "").strip().lower()),
        _KIND_PHASE_TILT.get(phase) if phase else None,
        _KIND_MINDSET_TILT.get(str(mindset or "").strip().lower()),
        _KIND_TACTIC_TILT.get(str(tactic or "").strip().lower()),
    )
    for tilt in tilts:
        if not tilt:
            continue
        for kind in weights:
            weights[kind] *= tilt.get(kind, 1.0)
    kinds = list(weights)
    return random.choices(kinds, weights=[weights[k] for k in kinds], k=1)[0]


def _is_keeper(player: dict) -> bool:
    return "wicket" in str(player.get("role") or "").strip().lower()


def _player_label(player: dict | None) -> str | None:
    if not player:
        return None
    return str(player.get("name") or "Player")


def build_dismissal(kind: str, bowler: dict | None, fielding_xi: list | None) -> dict:
    """Return {kind, fielder, text, bowler_credit} for a wicket of `kind`."""
    kind = str(kind or "caught").strip().lower()
    bowler = bowler or {}
    bowler_name = _player_label(bowler) or "Bowler"
    bowler_id = bowler.get("player_id")
    xi = [p for p in (fielding_xi or []) if isinstance(p, dict)]
    others = [p for p in xi if p.get("player_id") is None or p.get("player_id") != bowler_id]
    keepers = [p for p in others if _is_keeper(p)]

    fielder = None
    mode = assist = None
    if kind == "stumped" and not keepers:
        kind = "caught"
    if kind == "caught":
        if not others or random.random() < CAUGHT_AND_BOWLED_CHANCE:
            fielder, text = bowler_name, f"c & b {bowler_name}"
        else:
            fielder = _player_label(random.choice(others))
            text = f"c {fielder} b {bowler_name}"
    elif kind == "stumped":
        fielder = _player_label(random.choice(keepers))
        text = f"st †{fielder} b {bowler_name}"
    elif kind == "lbw":
        text = f"lbw b {bowler_name}"
    elif kind == "run_out":
                                                                              
                                                                        
        throwers = [p for p in others if not _is_keeper(p)] or others or xi
        fielder = _player_label(random.choice(throwers)) if throwers else None
        if fielder and keepers and random.random() < RUN_OUT_RELAY_CHANCE:
            keeper_label = _player_label(keepers[0])
            if keeper_label and keeper_label != fielder:
                mode, assist = "relay", keeper_label
        if assist:
            text = f"run out ({fielder}/{assist})"
        else:
            mode = "direct"
            text = f"run out ({fielder})" if fielder else "run out"
    else:          
        kind, text = "bowled", f"b {bowler_name}"
    return {"kind": kind, "fielder": fielder, "text": text, "mode": mode, "assist": assist,
            "bowler_credit": wicket_kind_credits_bowler(kind)}


def assign_dismissal(session: Any, outcome: Any) -> dict | None:
    """Decide the dismissal for a wicket ball and stamp it on `outcome`.

    Works on any /play-style runtime session (duck-typed).  Returns the
    dismissal dict, or None when the ball was not a wicket.
    """
    if not bool(getattr(outcome, "wicket", False)):
        return None
    bowler = getattr(session, "current_bowler", None) or {}
    xi = list(getattr(session, "bowling_squad", None) or [])[:11]
    fallen = getattr(getattr(getattr(session, "innings", None), "score", None), "wickets_fallen", []) or []
    run_outs = sum(1 for w in fallen if isinstance(w, dict) and w.get("type") == "run_out")
    kind = getattr(outcome, "wicket_kind", None)
    if bool(getattr(session, "free_hit_next_ball", False)):
                                                                               
                                                                              
        kind = "run_out"
    elif not kind or (kind == "run_out" and run_outs >= MAX_RUN_OUTS_PER_INNINGS):
        score = getattr(getattr(session, "innings", None), "score", None)
        try:
            over_now = int(getattr(score, "overs", 0) or 0) + 1
        except (TypeError, ValueError):
            over_now = None
        kind = pick_wicket_kind(bowler.get("bowling_hand"), bowler.get("role"), run_outs,
                                keeper_available=any(_is_keeper(p) for p in xi if isinstance(p, dict)),
                                pitch=getattr(session, "pitch", None), over_number=over_now,
                                tactic=getattr(session, "current_tactic", None))
    info = build_dismissal(kind, bowler, xi)
    try:
        outcome.wicket_kind = info["kind"]
        outcome.fielder = info["fielder"]
        outcome.dismissal_text = info["text"]
        outcome.dismissal_mode = info.get("mode")
        outcome.assist = info.get("assist")
    except Exception:
        pass
    return info
