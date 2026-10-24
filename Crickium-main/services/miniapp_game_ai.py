"""AI decision making for the Mini App "Play Game" mode.

Pure functions, no I/O.  Every choice the computer opponent makes goes
through here so the four difficulty levels can differ in *how well the AI
thinks*, on top of the probability differences that live in
engines/miniapp_tactics_engine.py.

What the AI may know when it decides
-------------------------------------
* the public match state (score, wickets, overs, target, pitch, levels)
* the user's choices from PREVIOUS balls (Expert studies them, Hard only
  uses phase/situation priors, Medium assumes nothing, Easy is mostly random)

What it can NEVER see
---------------------
* the choice the user is making on the CURRENT ball (batting approach or
  bowling tactic).  Both sides pick simultaneously, exactly like the bot.

Decision model
--------------
Each candidate option is scored with the real probability engine:

    utility(batting option)  =  E[runs]  -  P(wicket) * wicket_cost
    utility(bowling option)  =  P(wicket) * wicket_cost  -  E[runs]

where E[...] is averaged over the AI's belief about the user's hidden
choice and ``wicket_cost`` is the number of runs a wicket is worth at the
current stage (a simple resource model, early wickets are cheap, late
wickets before the death overs are expensive).  The final pick is a
softmax over the utilities; the temperature and the amount of pure noise
come from the difficulty profile (``ai`` block).
"""

from __future__ import annotations

import math
import random
from typing import Any, Iterable

import engines.miniapp_tactics_engine as eng

MINDSET_KEYS: tuple[str, ...] = tuple(eng.BATTER_MINDSETS)

TACTIC_KEYS: dict[str, tuple[str, ...]] = {
    "pace": ("defensive", "swinging", "pace_up", "back_of_length", "variation"),
    "off": ("off_break", "doosra", "arm_ball", "carrom_ball", "top_spin"),
    "leg": ("leg_breaker", "top_spinner", "slider", "flipper", "googly_ball"),
}

# Share of a batting side's resources (wickets in hand) that is still left.
_RESOURCE = (1.00, 0.985, 0.955, 0.91, 0.845, 0.76, 0.65, 0.52, 0.37, 0.20, 0.0)

_BASE_MINDSET_PRIOR = {
    "defensive": 0.08, "rotate": 0.27, "neutral": 0.30,
    "aggressive": 0.25, "ultra_aggressive": 0.10,
}


# --------------------------------------------------------------------------
# small shared helpers (also used by the core engine and the win-probability
# model, which keep a one-way dependency on this module)
# --------------------------------------------------------------------------
def tactic_set_key(bowling_hand: str | None) -> str:
    """Which button set a bowler gets - identical to the bot's keyboard."""
    style = str(bowling_hand or "").strip().upper()
    if style in {"RAO", "LAO"} or "OFF BREAK" in style or "OFFSPIN" in style:
        return "off"
    if style in {"RAL", "LAL"} or "LEG SPIN" in style or "LEGSPIN" in style:
        return "leg"
    return "pace"


def par_rpo(pitch: str, overs: int) -> float:
    """Typical run-rate for the pitch in this format (a soft reference)."""
    return eng.pitch_target_rpo(pitch) * float(eng.format_profile(overs)["rpo_scale"])


def wicket_cost(balls_left: int, wickets_lost: int, par: float) -> float:
    """Runs a wicket is worth right now (resource model)."""
    w = max(0, min(9, int(wickets_lost)))
    return max(0.0, par / 6.0 * max(0, int(balls_left) - 1) * (_RESOURCE[w] - _RESOURCE[w + 1]))


def phase_of(over_no: int, overs: int) -> str:
    eq = eng.equivalent_over(over_no, overs)
    if eq <= 6:
        return "pp"
    if eq <= 15:
        return "mid"
    return "death"


def _normalise(dist: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in dist.values())
    if total <= 0:
        n = len(dist) or 1
        return {k: 1.0 / n for k in dist}
    return {k: max(0.0, v) / total for k, v in dist.items()}


def _softmax_pick(options: list[str], utilities: list[float], temperature: float,
                  random_pick: float, rng: random.Random) -> str:
    """Epsilon-random + softmax choice.  temperature<=0 -> argmax."""
    if not options:
        raise ValueError("no options")
    if random_pick > 0 and rng.random() < random_pick:
        return rng.choice(options)
    if temperature <= 1e-9:
        best = max(range(len(options)), key=lambda i: utilities[i])
        return options[best]
    top = max(utilities)
    weights = [math.exp((u - top) / temperature) for u in utilities]
    return rng.choices(options, weights=weights, k=1)[0]


def _ai_knobs(difficulty: str) -> dict:
    return eng.difficulty_profile(difficulty)["ai"]


# --------------------------------------------------------------------------
# beliefs about the user's hidden choice
# --------------------------------------------------------------------------
def mindset_prior(frac: float, wickets: int, ratio: float | None) -> dict[str, float]:
    """What a sensible captain expects a batter to do in this situation.

    frac   - how far through the innings we are (0..1)
    ratio  - required run-rate / par run-rate in a chase (None in innings 1)
    """
    p = dict(_BASE_MINDSET_PRIOR)
    if frac < 0.30:
        p["aggressive"] *= 1.15
        p["neutral"] *= 1.05
    elif frac > 0.80:
        p["aggressive"] *= 1.5
        p["ultra_aggressive"] *= 2.0
        p["defensive"] *= 0.5
        p["rotate"] *= 0.8
    if wickets >= 6:
        p["rotate"] *= 1.5
        p["defensive"] *= 1.6
        p["ultra_aggressive"] *= 0.6
    elif wickets <= 1 and frac < 0.5:
        p["aggressive"] *= 1.1
    if ratio is not None:
        if ratio > 1.4:
            p["aggressive"] *= 1.7
            p["ultra_aggressive"] *= 2.6
            p["defensive"] *= 0.3
            p["rotate"] *= 0.6
        elif ratio > 1.1:
            p["aggressive"] *= 1.35
            p["ultra_aggressive"] *= 1.5
        elif ratio < 0.8:
            p["rotate"] *= 1.5
            p["neutral"] *= 1.2
            p["ultra_aggressive"] *= 0.4
    return _normalise(p)


def _decayed_counts(history: Iterable[str], keys: Iterable[str], decay: float = 0.92) -> dict[str, float]:
    counts = {k: 0.0 for k in keys}
    hist = list(history)
    weight = 1.0
    for item in reversed(hist[-30:]):
        if item in counts:
            counts[item] += weight
        weight *= decay
    return counts


def belief_over_mindsets(kind: str, frac: float, wickets: int, ratio: float | None,
                         history: list[str]) -> dict[str, float]:
    prior = mindset_prior(frac, wickets, ratio)
    if kind == "none" or kind == "uniform":
        return {k: 1.0 / len(MINDSET_KEYS) for k in MINDSET_KEYS}
    if kind == "phase":
        return prior
    # "history": blend the situational prior with what the user actually does
    counts = _decayed_counts(history, MINDSET_KEYS)
    total = sum(counts.values())
    if total < 3:
        return prior
    weight = min(0.65, 0.10 * total)  # trust grows with the sample size
    emp = {k: counts[k] / total for k in MINDSET_KEYS}
    return _normalise({k: (1 - weight) * prior[k] + weight * emp[k] for k in MINDSET_KEYS})


def belief_over_tactics(kind: str, history: list[str]) -> dict[str, float]:
    """Belief over the bowler's tactic *archetype* (the engine maps every
    spinner delivery onto one of the five archetypes)."""
    archetypes = TACTIC_KEYS["pace"]
    uniform = {k: 1.0 / len(archetypes) for k in archetypes}
    if kind != "history":
        return uniform
    counts = _decayed_counts([eng.SPINNER_TACTIC_ARCHETYPE.get(h, h) for h in history], archetypes)
    total = sum(counts.values())
    if total < 3:
        return uniform
    weight = min(0.65, 0.10 * total)
    return _normalise({k: (1 - weight) * uniform[k] + weight * counts[k] / total for k in archetypes})


# --------------------------------------------------------------------------
# expected-value helpers built on the real probability engine
# --------------------------------------------------------------------------
_RUN_VALUE = {0: 0.0, 1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 6: 6.0, "WD": 1.0, "NB": 1.0, "LB": 1.0, "BY": 1.0}


def _ball_value(ctx: dict, tactic: str, mindset: str) -> tuple[float, float]:
    """(expected runs, wicket probability) for one delivery."""
    weights = eng.resolve_weights(
        tactic, mindset, ctx["pitch"], ctx["over_no"], ctx["batter_level"], ctx["bowler_level"],
        ctx["confidence"],
        batsman_balls_faced=ctx["balls_faced"], wickets_this_over=ctx["wkts_over"],
        bowler_style=ctx["bowler_style"], bowler_role=ctx["bowler_role"], batter_role=ctx["batter_role"],
        target=ctx["target"], total_runs=ctx["runs"], balls_remaining=ctx["balls_left"],
        wickets_in_hand=10 - ctx["wkts"], batting_position=ctx["position"],
        over_runs=ctx["over_runs"], free_hit_next_ball=ctx["free_hit"],
        batting_upgrade_context=ctx.get("bat_upg"), bowling_upgrade_context=ctx.get("bowl_upg"),
        max_overs=ctx["overs"], difficulty=ctx["difficulty"], batter_side=ctx["batter_side"],
    )
    probs = eng.outcome_probabilities(weights)
    exp_runs = sum(_RUN_VALUE[k] * probs[k] for k in _RUN_VALUE)
    return exp_runs, probs["W"]


def _situation(ctx: dict) -> tuple[float, float, float | None]:
    """(par rpo, wicket cost in runs, chase ratio or None)."""
    par = par_rpo(ctx["pitch"], ctx["overs"])
    cost = wicket_cost(ctx["balls_left"], ctx["wkts"], par)
    ratio = None
    if ctx["target"] is not None and ctx["balls_left"] > 0:
        needed = max(0, ctx["target"] - ctx["runs"])
        ratio = (needed * 6.0 / ctx["balls_left"]) / max(0.1, par)
    return par, cost, ratio


def _frac(ctx: dict) -> float:
    total = ctx["overs"] * 6
    return min(1.0, max(0.0, 1.0 - ctx["balls_left"] / float(total)))


# --------------------------------------------------------------------------
# public decisions
# --------------------------------------------------------------------------
def batting_intent(ctx: dict) -> float:
    """How hard a sensible batter wants to attack right now, on the scale
    0 (defensive) .. 4 (ultra aggressive).  Pure situational cricket sense:
    phase of the innings, wickets in hand, how settled the batter is and,
    in a chase, the required run-rate against what the pitch normally gives.
    """
    phase = phase_of(ctx["over_no"], ctx["overs"])
    base = {"pp": 2.6, "mid": 2.1, "death": 3.4}[phase]
    wickets = int(ctx["wkts"])
    intent = base

    if wickets >= 7:
        intent -= 1.0
    elif wickets >= 5:
        intent -= 0.6 if phase != "death" else 0.3
    elif wickets >= 3 and phase == "pp":
        intent -= 0.5

    faced = int(ctx["balls_faced"])
    if faced < 6:
        intent -= 0.5                      # play yourself in
    elif faced >= 18 and float(ctx["confidence"]) >= 50:
        intent += 0.4                      # set batter can open up
    if int(ctx["batter_level"]) < 45 and phase != "death":
        intent -= 0.4                      # tail-ender: survive

    par, _cost, ratio = _situation(ctx)
    if ratio is not None:
        chase = 1.9 + 2.3 * (ratio - 0.85)
        if wickets >= 7 and ratio < 1.3:
            chase -= 0.6
        if ctx["balls_left"] <= 12 and ratio > 1.0:
            chase += 0.5
        intent = 0.35 * intent + 0.65 * chase
    else:
        # first innings: chase the pitch's par score
        played = ctx["overs"] * 6 - ctx["balls_left"]
        if played >= 12:
            crr = ctx["runs"] * 6.0 / played
            if crr < par * 0.85:
                intent += 0.3
            elif crr > par * 1.15:
                intent -= 0.2
    return max(0.3, min(4.0, intent))


def pick_mindset(ctx: dict, history: list[str], rng: random.Random) -> str:
    """AI is BATTING: choose the batting approach for the next ball/over.

    ctx keys: pitch, overs, over_no, difficulty, batter_side, batter_level,
    bowler_level, bowler_style, bowler_role, batter_role, confidence,
    balls_faced, wkts_over, target, runs, balls_left, wkts, position,
    over_runs, free_hit [, bat_upg, bowl_upg, skill].
    `skill` (optional) names the difficulty whose decision knobs to use
    (tests use it to emulate a human); the probabilities always follow
    ctx["difficulty"].
    `history` = the user's recent bowling tactics (oldest first).

    The pick starts from the situational intent (see batting_intent); harder
    levels additionally lean towards the options the real probability engine
    rates higher, using their belief about the user's hidden tactic.
    """
    knobs = _ai_knobs(ctx.get("skill") or ctx["difficulty"])
    intent = batting_intent(ctx) + rng.gauss(0.0, float(knobs["bat_noise"]))
    scores = [-1.2 * abs(i - intent) for i in range(len(MINDSET_KEYS))]

    util_w = float(knobs["bat_util"])
    if util_w > 0:
        _par, cost, ratio = _situation(ctx)
        if ratio is not None:
            if ratio > 1.4:
                cost *= 0.55          # must attack: wickets are cheap
            elif ratio < 0.65:
                cost *= 1.45          # cruising: do not throw it away
        belief = belief_over_tactics(knobs["belief"], history)
        utilities = []
        for mindset in MINDSET_KEYS:
            exp_runs = 0.0
            p_wicket = 0.0
            for tactic, q in belief.items():
                r, w = _ball_value(ctx, tactic, mindset)
                exp_runs += q * r
                p_wicket += q * w
            utilities.append(exp_runs - p_wicket * cost)
        top = max(utilities)
        scores = [s + util_w * (u - top) for s, u in zip(scores, utilities)]

    return _softmax_pick(list(MINDSET_KEYS), scores, float(knobs["bat_temp"]),
                         float(knobs["bat_random"]), rng)


def pick_tactic(ctx: dict, history: list[str], rng: random.Random) -> str:
    """AI is BOWLING: choose the delivery for the next ball/over.
    `history` = the user's recent batting approaches (oldest first)."""
    knobs = _ai_knobs(ctx.get("skill") or ctx["difficulty"])
    _par, cost, ratio = _situation(ctx)
    frac = _frac(ctx)
    belief = belief_over_mindsets(knobs["belief"], frac, ctx["wkts"], ratio, history)
    keys = TACTIC_KEYS[tactic_set_key(ctx["bowler_style"])]
    value = cost
    if ratio is not None and ratio < 0.85:
        value *= 1.2     # the batting side is cruising - only wickets will stop them
    utilities = []
    for tactic in keys:
        exp_runs = 0.0
        p_wicket = 0.0
        for mindset, q in belief.items():
            r, w = _ball_value(ctx, tactic, mindset)
            exp_runs += q * r
            p_wicket += q * w
        utilities.append(p_wicket * value - exp_runs)
    return _softmax_pick(list(keys), utilities, float(knobs["bowl_temp"]), float(knobs["bowl_random"]), rng)


def pick_bowler(difficulty: str, candidates: list[dict], ctx: dict, rng: random.Random) -> int:
    """AI is BOWLING: pick who bowls the next over.

    candidates: [{pid, bowl, bw, role, overs_left, wkts, runs, balls}, ...]
    ctx: pitch, overs, over_no, balls_left, wkts_lost (of the batting side)
    """
    if not candidates:
        raise ValueError("no bowler available")
    knobs = _ai_knobs(difficulty)
    mode = knobs["bowler_choice"]
    if mode == "random" or len(candidates) == 1:
        return int(rng.choice(candidates)["pid"])

    pitch = ctx["pitch"]
    phase = phase_of(ctx["over_no"], ctx["overs"])

    def fit(c: dict) -> float:
        family = eng.bowler_family(c.get("bw"), c.get("role"))
        bonus = 0.0
        if family == "pace" and pitch in {"green", "bouncy"}:
            bonus += 5.0
        if family == "spin" and pitch in {"dry", "dusty", "slow"}:
            bonus += 5.0
        if mode in {"matchup", "plan"}:
            if phase == "pp" and family == "pace":
                bonus += 2.0
            if phase == "mid" and family == "spin":
                bonus += 2.0
            if phase == "death" and family == "pace":
                bonus += 3.0
        return bonus

    if mode == "level":
        scores = [float(c["bowl"]) for c in candidates]
        pick = _softmax_pick([str(c["pid"]) for c in candidates], scores, 6.0, 0.0, rng)
        return int(pick)

    # matchup / plan
    ranked = sorted(candidates, key=lambda c: -float(c["bowl"]))
    top_two = {int(c["pid"]) for c in ranked[:2]}
    total_overs = int(ctx["overs"])
    death_overs_left = sum(
        1 for o in range(ctx["over_no"], total_overs + 1)
        if phase_of(o, total_overs) == "death"
    )
    scores = []
    for c in candidates:
        score = float(c["bowl"]) + fit(c)
        balls = int(c.get("balls") or 0)
        if balls >= 6:
            econ = (int(c.get("runs") or 0) * 6.0) / balls
            score += 1.5 * int(c.get("wkts") or 0)
            if econ > par_rpo(pitch, total_overs) * 1.35:
                score -= 3.0
        if mode == "plan":
            if phase != "death" and int(c["pid"]) in top_two and int(c["overs_left"]) <= death_overs_left:
                score -= 4.0      # save the best bowlers for the end
            if phase == "death" and int(c["pid"]) in top_two:
                score += 4.0
        scores.append(score)
    noise = 0.8 if mode == "plan" else 2.0
    noisy = [s + rng.gauss(0.0, noise) for s in scores]
    best = max(range(len(candidates)), key=lambda i: noisy[i])
    return int(candidates[best]["pid"])


def pick_toss_decision(difficulty: str, pitch: str, rng: random.Random) -> str:
    """The AI's bat/bowl call after winning the toss.  Mostly a coin flip;
    harder levels let the pitch nudge it a little."""
    knobs = _ai_knobs(difficulty)
    if rng.random() < float(knobs.get("toss_informed", 0.0)):
        p_bat = {"green": 0.15, "bouncy": 0.20, "flat": 0.35, "hard": 0.40, "even": 0.45,
                 "slow": 0.60, "dry": 0.65, "dusty": 0.70}.get(pitch, 0.5)
        return "bat" if rng.random() < p_bat else "bowl"
    return rng.choice(["bat", "bowl"])
