"""Live win probability for the Mini App "Play Game" mode.

A Monte-Carlo model that plays the rest of the match thousands of ball
outcomes ahead using the SAME probability engine as the real game
(engines/miniapp_tactics_engine.py).  To keep it fast the engine is asked once
per situation "cell" (over bracket, batter level, bowler level, batter
settledness, batting position, chase pressure) and the answer is cached
for every later rollout and every other game.

Design notes
------------
* Common random numbers: rollout ``i`` uses the same pre-drawn uniforms every
  time, so consecutive estimates move smoothly (a dot ball nudges the bar, it
  does not jitter it).
* Second innings  : rollouts start from the live state.
* First innings   : rollouts give the final-score distribution; the chance of
  the other side chasing that down comes from a chase curve computed once per
  game (the squads and pitch do not change).
* Before the toss : both batting orders are averaged.
* Behaviour model : batters follow a situational approach (phase, wickets,
  required rate) like a sensible captain, the bowling side mixes its tactics.
  The user's own recent style is blended in once enough balls were seen.

Pure Python (no numpy) - a typical estimate takes tens of milliseconds once
the cell cache is warm.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left
from typing import Any

import engines.miniapp_tactics_engine as eng
import services.miniapp_game_ai as ai
from services.miniapp_game_squads import can_bowl

N_SIMS = 420
_MAX_DELIVERIES = 330            # per innings, incl. extras
_CELLS: dict[tuple, tuple] = {}
_MIND_CUM: dict[tuple, list[float]] = {}
_INTENT: dict[tuple, float] = {}
_UNI: list[list[float]] = []
_CURVE_TARGETS_STEP = 8
_MINDSETS = tuple(eng.BATTER_MINDSETS)
_OUT = tuple(eng.OUTCOMES)
_PACE_TACTICS = ai.TACTIC_KEYS["pace"]
_MAX_CELLS = 60000


def _uniforms() -> list[list[float]]:
    global _UNI
    if not _UNI:
        rnd = random.Random(20260611)
        _UNI = [[rnd.random() for _ in range(2 * _MAX_DELIVERIES * 2)] for _ in range(N_SIMS)]
    return _UNI


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------
_EQ_REP = {1: 1, 2: 2, 3: 2, 4: 5, 5: 5, 6: 5, 7: 8, 8: 8, 9: 8, 10: 8,
           11: 13, 12: 13, 13: 13, 14: 13, 15: 13, 16: 16, 17: 16, 18: 18, 19: 18, 20: 20}
_BF_REP = ((5, 0, 0.0), (15, 8, 12.0), (30, 22, 32.0), (10 ** 6, 40, 52.0))   # (upper, balls, confidence)
_POS_REP = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 11: 11}


def _lvl_bin(level: float, step: int = 6) -> int:
    return max(1, min(99, int(round(level / step)) * step))


def _bf_bucket(balls_faced: int) -> int:
    for i, (upper, _b, _c) in enumerate(_BF_REP):
        if balls_faced <= upper:
            return i
    return len(_BF_REP) - 1


def _pressure_bin(ratio: float | None) -> int:
    if ratio is None:
        return 0
    if ratio < 0.9:
        return 1
    if ratio < 1.2:
        return 2
    if ratio < 1.6:
        return 3
    return 4


_PRESSURE_RATIO = {0: None, 1: 0.7, 2: 1.05, 3: 1.4, 4: 1.9}


_OVER_SCALES = (1.0, 0.55, 0.30, 0.15, 0.03)
_SCORE_IDX = tuple(i for i, o in enumerate(_OUT) if o in (1, 2, 3, 4, 6, "WD", "NB", "LB", "BY"))


def _to_cums(vecs: list[list[float]], scale: float) -> tuple:
    out = []
    for vec in vecs:
        v = list(vec)
        if scale < 1.0:
            source = sum(v[i] for i in _SCORE_IDX)
            cut = source * (1.0 - scale)
            for i in _SCORE_IDX:
                v[i] *= scale
            v[_OUT.index(0)] += cut * 0.80
            v[_OUT.index(1)] += cut * 0.20
        run = 0.0
        cum = []
        for x in v:
            run += x
            cum.append(run)
        cum[-1] = 1.0 + 1e-9
        out.append(tuple(cum))
    return tuple(out)


def _cums_for(cell: tuple, scale_idx: int) -> tuple:
    got = cell[1][scale_idx]
    if got is None:
        got = _to_cums(cell[4], _OVER_SCALES[scale_idx])
        cell[1][scale_idx] = got
    return got


def _over_scale_idx(over_runs: float) -> int:
    if over_runs >= 18:
        return 4
    if over_runs >= 15:
        return 3
    if over_runs >= 12:
        return 2
    if over_runs >= 8:
        return 1
    return 0


def _knobs(skill: str) -> dict:
    return eng.difficulty_profile(skill)["ai"]


def _cell(cfg: tuple, side: str, eq: int, bat_lvl: int, bowl_lvl: int, bf_b: int, pos: int,
          press: int, family: str):
    """One situation cell: (key, cumulative outcome distribution per batting
    approach, batting utility per approach).

    The bowling side's tactic mix is NOT uniform: like the real opponent it
    leans towards the tactics that hurt the batter most (softmax over the
    bowling utility with the temperature of that side's decision skill)."""
    difficulty, overs, pitch = cfg
    bat_skill = difficulty if side == "ai" else "medium"
    bowl_skill = "medium" if side == "ai" else difficulty
    key = (cfg, side, eq, bat_lvl, bowl_lvl, bf_b, pos, press, family)
    hit = _CELLS.get(key)
    if hit is not None:
        return hit
    over_no = max(1, min(overs, int(round(1 + (eq - 1) * (overs - 1) / 19.0)))) if overs != 20 else eq
    bf_balls, bf_conf = _BF_REP[bf_b][1], _BF_REP[bf_b][2]
    balls_total = overs * 6
    balls_left = max(1, balls_total - (over_no - 1) * 6 - 3)
    ratio = _PRESSURE_RATIO[press]
    par = ai.par_rpo(pitch, overs)
    target = None
    if ratio is not None:
        target = max(2, int(round(ratio * par * balls_left / 6.0)))
    tactics = _PACE_TACTICS if family != "spin" else ai.TACTIC_KEYS["off"]
    style = "RAF" if family != "spin" else "RAO"
    wkts = max(0, min(9, pos - 2))
    cost = ai.wicket_cost(balls_left, wkts, par)
    if ratio is not None and ratio < 0.85:
        cost *= 1.2
    probs: dict[tuple[int, int], list[float]] = {}
    ev: dict[tuple[int, int], tuple[float, float]] = {}
    for mi, mindset in enumerate(_MINDSETS):
        for ti, tactic in enumerate(tactics):
            weights = eng.resolve_weights(
                tactic, mindset, pitch, over_no, bat_lvl, bowl_lvl, bf_conf,
                batsman_balls_faced=bf_balls, wickets_this_over=0, bowler_style=style, bowler_role="Bowler",
                batter_role="Batsman", target=target, total_runs=0, balls_remaining=balls_left,
                wickets_in_hand=10 - wkts, batting_position=pos, over_runs=0,
                max_overs=overs, difficulty=difficulty, batter_side=side,
            )
            tot = sum(max(0.0, weights.get(o, 0.0)) for o in _OUT) or 1.0
            vec = [max(0.0, weights.get(o, 0.0)) / tot for o in _OUT]
            probs[(mi, ti)] = vec
            runs = sum(v * (o if isinstance(o, int) else 1.0) for v, o in zip(vec, _OUT) if o != "W")
            ev[(mi, ti)] = (runs, vec[_OUT.index("W")])
    # bowling side: tactic weights against a uniform guess of the batter's approach
    kb = _knobs(bowl_skill)
    u_t = []
    for ti in range(len(tactics)):
        runs = sum(ev[(mi, ti)][0] for mi in range(5)) / 5.0
        pw = sum(ev[(mi, ti)][1] for mi in range(5)) / 5.0
        u_t.append(pw * cost - runs)
    top = max(u_t)
    temp = max(0.02, float(kb["bowl_temp"]))
    wt = [math.exp((u - top) / temp) for u in u_t]
    tot = sum(wt)
    eps = float(kb["bowl_random"])
    wt = [(1 - eps) * w / tot + eps / len(wt) for w in wt]
    vecs = []
    utils = []
    for mi in range(5):
        acc = [0.0] * len(_OUT)
        runs = pw = 0.0
        for ti, w in enumerate(wt):
            vec = probs[(mi, ti)]
            for i, v in enumerate(vec):
                acc[i] += w * v
            runs += w * ev[(mi, ti)][0]
            pw += w * ev[(mi, ti)][1]
        vecs.append(acc)
        utils.append(runs - pw * cost)
    # cums[scale_idx][mindset]: the engine's "over-run rarity" shrinks the
    # scoring mass once the current over is already expensive
    cums_by_scale = [None] * len(_OVER_SCALES)
    cums_by_scale[0] = _to_cums(vecs, 1.0)
    result = (key, cums_by_scale, tuple(utils), bat_skill, vecs)
    if len(_CELLS) > _MAX_CELLS:
        _CELLS.clear()
        _MIND_CUM.clear()
    _CELLS[key] = result
    return result


def _intent(overs: int, over_no: int, wkts: int, balls_faced: int, bat_lvl: int,
            ratio: float | None, balls_left: int) -> float:
    key = (overs, over_no, min(wkts, 9), min(balls_faced // 6, 6), bat_lvl // 8,
           None if ratio is None else round(ratio * 5) / 5.0, min(balls_left // 6, 4))
    hit = _INTENT.get(key)
    if hit is not None:
        return hit
    phase = ai.phase_of(over_no, overs)
    base = {"pp": 2.6, "mid": 2.1, "death": 3.4}[phase]
    intent = base
    if wkts >= 7:
        intent -= 1.0
    elif wkts >= 5:
        intent -= 0.6 if phase != "death" else 0.3
    elif wkts >= 3 and phase == "pp":
        intent -= 0.5
    if balls_faced < 6:
        intent -= 0.5
    elif balls_faced >= 18:
        intent += 0.2
    if bat_lvl < 45 and phase != "death":
        intent -= 0.4
    if ratio is not None:
        chase = 1.9 + 2.3 * (ratio - 0.85)
        if wkts >= 7 and ratio < 1.3:
            chase -= 0.6
        if balls_left <= 12 and ratio > 1.0:
            chase += 0.5
        intent = 0.35 * intent + 0.65 * chase
    intent = max(0.3, min(4.0, intent))
    if len(_INTENT) > 40000:
        _INTENT.clear()
    _INTENT[key] = intent
    return intent


def _mind_cum(cell: tuple, intent: float, ratio: float | None) -> list[float]:
    """Probability of each batting approach in this cell: situational intent
    plus (for the smarter levels) a pull towards the higher-utility options,
    exactly the shape of services.miniapp_game_ai.pick_mindset."""
    q = round(intent * 4) / 4.0
    ck = (cell[0], q)
    hit = _MIND_CUM.get(ck)
    if hit is not None:
        return hit
    kb = _knobs(cell[3])
    utils = cell[2]
    scores = [-1.2 * abs(i - q) for i in range(5)]
    uw = float(kb["bat_util"])
    if uw > 0:
        top = max(utils)
        scores = [sc + uw * (u - top) for sc, u in zip(scores, utils)]
    temp = max(0.05, float(kb["bat_temp"]))
    top_s = max(scores)
    w = [math.exp((sc - top_s) / temp) for sc in scores]
    tot = sum(w)
    eps = float(kb["bat_random"])
    w = [(1 - eps) * x / tot + eps / 5.0 for x in w]
    cum, run = [], 0.0
    for x in w:
        run += x
        cum.append(run)
    cum[-1] = 1.0 + 1e-9
    _MIND_CUM[ck] = cum
    return cum


# ---------------------------------------------------------------------------
# rollouts
# ---------------------------------------------------------------------------
def _attack_levels(xi: list[dict], overs: int, quota: int, start_over: int = 1) -> tuple[list[int], str]:
    """Bowling level for each over from `start_over` on, cycling the best
    bowlers the way a captain would, plus the dominant family."""
    bowlers = sorted((p for p in xi if can_bowl(p)), key=lambda p: -int(p["bowl"]))
    if len(bowlers) < 2:
        others = sorted((p for p in xi if p not in bowlers), key=lambda p: -int(p["bowl"]))
        bowlers = bowlers + others[:max(0, 5 - len(bowlers))]
    need = max(1, -(-overs // quota))
    rota = bowlers[:max(need, min(5, len(bowlers)))] or xi[:1]
    levels = []
    for o in range(start_over, overs + 1):
        levels.append(max(20, int(rota[(o - 1) % len(rota)]["bowl"])))
    spin = sum(1 for p in rota if eng.bowler_family(p.get("bw"), p.get("role")) == "spin")
    return levels, ("spin" if spin * 2 > len(rota) else "pace")


def _rollout(cfg: tuple, side: str, overs: int, order_lvls: list[int], attack: list[int], family: str,
             uni: list[float], u_off: int, *, runs: int, wkts: int, balls: int, striker: int, non: int,
             nxt: int, bf: dict[int, int], target: int | None, over_runs: int, bowl_start_over: int,
             style_shift: float = 0.0) -> tuple[int, int, int]:
    """Plays one innings to the end.  Returns (runs, wkts, deliveries_used)."""
    difficulty, _ov, pitch = cfg
    total_balls = overs * 6
    d = 0
    legal = balls
    bf = dict(bf)
    run_scale = float(eng.format_profile(overs)["run_scale"])
    cur_over_runs = int(over_runs)
    n_lv = len(order_lvls)
    par = ai.par_rpo(pitch, overs)
    while legal < total_balls and wkts < 10 and d < _MAX_DELIVERIES - 2:
        over_no = legal // 6 + 1
        eq = _EQ_REP[eng.equivalent_over(over_no, overs)]
        a_idx = min(len(attack) - 1, max(0, over_no - bowl_start_over))
        bowl_lvl = _lvl_bin(attack[a_idx], 5)
        b_idx = min(striker, n_lv - 1)
        bat_lvl = _lvl_bin(order_lvls[b_idx])
        faced = bf.get(striker, 0)
        balls_left = total_balls - legal
        ratio = None
        pbin = 0
        if target is not None:
            need = target - runs
            if need <= 0:
                break
            ratio = (need * 6.0 / max(1, balls_left)) / max(0.1, par)
            pbin = _pressure_bin(ratio)
        intent = _intent(overs, over_no, wkts, faced, bat_lvl, ratio, balls_left) + style_shift
        cell = _cell(cfg, side, eq, bat_lvl, bowl_lvl, _bf_bucket(faced), min(striker + 1, 11), pbin, family)
        mc = _mind_cum(cell, max(0.3, min(4.0, intent)), ratio)
        u1 = uni[u_off + 2 * d]
        u2 = uni[u_off + 2 * d + 1]
        mi = bisect_left(mc, u1)
        out = _OUT[min(len(_OUT) - 1, bisect_left(_cums_for(cell, _over_scale_idx(cur_over_runs / run_scale))[mi], u2))]
        d += 1
        runs_before, legal_before = runs, legal
        if out == "W":
            wkts += 1
            legal += 1
            bf[striker] = faced + 1
            if wkts >= 10:
                break
            striker = nxt
            nxt += 1
            if legal % 6 == 0:
                striker, non = non, striker
        elif out == "WD" or out == "NB":
            runs += 1
        elif out == "BY" or out == "LB":
            runs += 1
            legal += 1
            bf[striker] = faced + 1
            striker, non = non, striker
            if legal % 6 == 0:
                striker, non = non, striker
        else:
            runs += out
            legal += 1
            bf[striker] = faced + 1
            if out % 2 == 1:
                striker, non = non, striker
            if legal % 6 == 0:
                striker, non = non, striker
        cur_over_runs += runs - runs_before
        if legal > legal_before and legal % 6 == 0:
            cur_over_runs = 0
    return runs, wkts, d


# ---------------------------------------------------------------------------
# game helpers
# ---------------------------------------------------------------------------
def _team_arrays(g: dict, side: str) -> tuple[list[int], list[dict]]:
    xi = g["teams"][side]["xi"]
    return [int(p["bat"]) for p in xi], xi


def _style_shift(g: dict, side: str) -> float:
    """How much more (or less) aggressive the user plays than the model's
    baseline, from the last balls actually played (0 for the computer)."""
    if side != "user":
        return 0.0
    hist = g.get("hist", {}).get("user_bat", [])
    if len(hist) < 12:
        return 0.0
    idx = {k: i for i, k in enumerate(_MINDSETS)}
    recent = [idx[h] for h in hist[-30:] if h in idx]
    if not recent:
        return 0.0
    mean = sum(recent) / len(recent)
    return max(-1.0, min(1.0, (mean - 2.5) * 0.45))


def _chase_curve(g: dict, chaser: str) -> list[tuple[int, float]]:
    """P(chasing side wins) for a ladder of targets, from fresh innings."""
    cfg = (g["cfg"]["difficulty"], int(g["cfg"]["overs"]), g["cfg"]["pitch"])
    overs = cfg[1]
    quota = eng.bowler_over_quota(overs)
    bat_lv, _xi = _team_arrays(g, chaser)
    fielder = "ai" if chaser == "user" else "user"
    attack, family = _attack_levels(g["teams"][fielder]["xi"], overs, quota)
    uni = _uniforms()
    par = ai.par_rpo(cfg[2], overs)
    lo = max(8, int(par * overs * 0.45))
    hi = int(par * overs * 1.9)
    step = max(2, int(round((hi - lo) / 14.0)))
    pts = []
    sims = 120
    shift = _style_shift(g, chaser)
    for t in range(lo, hi + step, step):
        wins = 0
        for i in range(sims):
            r, _w, _d = _rollout(cfg, chaser, overs, bat_lv, attack, family, uni[i], 0,
                                 runs=0, wkts=0, balls=0, striker=0, non=1, nxt=2, bf={}, target=t,
                                 over_runs=0, bowl_start_over=1, style_shift=shift)
            if r >= t:
                wins += 1
        pts.append((t, (wins + 0.5) / (sims + 1.0)))
    # monotone (higher target -> lower chance) and smooth
    out = []
    best = 1.0
    for t, p in pts:
        best = min(best, p)
        out.append((t, best))
    return out


def _curve_at(curve: list[tuple[int, float]], target: float) -> float:
    if not curve:
        return 0.5
    if target <= curve[0][0]:
        return curve[0][1]
    if target >= curve[-1][0]:
        return curve[-1][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t0 <= target <= t1:
            f = (target - t0) / float(t1 - t0 or 1)
            return p0 + f * (p1 - p0)
    return curve[-1][1]


def _innings_samples(g: dict, side: str, inn: dict | None, n: int) -> list[int]:
    """Final first-innings totals for `side` from the live state (or fresh)."""
    cfg = (g["cfg"]["difficulty"], int(g["cfg"]["overs"]), g["cfg"]["pitch"])
    overs = cfg[1]
    quota = eng.bowler_over_quota(overs)
    fielder = "ai" if side == "user" else "user"
    bat_lv, _xi = _team_arrays(g, side)
    attack, family = _attack_levels(g["teams"][fielder]["xi"], overs, quota)
    uni = _uniforms()
    shift = _style_shift(g, side)
    out = []
    for i in range(n):
        if inn is None or inn["idx"] != g["n"] or inn["bat"] != side:
            r, _w, _d = _rollout(cfg, side, overs, bat_lv, attack, family, uni[i], 0, runs=0, wkts=0, balls=0,
                                 striker=0, non=1, nxt=2, bf={}, target=None, over_runs=0, bowl_start_over=1,
                                 style_shift=shift)
        else:
            bf = {k: b["balls"] for k, b in enumerate(inn["batters"]) if b["balls"]}
            r, _w, _d = _rollout(cfg, side, overs, bat_lv, attack, family, uni[i], 2 * len(inn["log"]),
                                 runs=inn["runs"], wkts=inn["wkts"], balls=inn["balls"], striker=inn["striker"],
                                 non=inn["non"], nxt=inn["next"], bf=bf, target=None,
                                 over_runs=inn["over"]["runs"], bowl_start_over=1, style_shift=shift)
        out.append(r)
    return out


def _chase_wins(g: dict, inn: dict, n: int) -> int:
    cfg = (g["cfg"]["difficulty"], int(g["cfg"]["overs"]), g["cfg"]["pitch"])
    overs = cfg[1]
    quota = eng.bowler_over_quota(overs)
    side = inn["bat"]
    fielder = inn["bowl"]
    bat_lv, _xi = _team_arrays(g, side)
    attack, family = _attack_levels(g["teams"][fielder]["xi"], overs, quota)
    uni = _uniforms()
    shift = _style_shift(g, side)
    bf = {k: b["balls"] for k, b in enumerate(inn["batters"]) if b["balls"]}
    off = 2 * len(inn["log"])
    wins = ties = 0
    for i in range(n):
        r, _w, _d = _rollout(cfg, side, overs, bat_lv, attack, family, uni[i], off, runs=inn["runs"],
                             wkts=inn["wkts"], balls=inn["balls"], striker=inn["striker"], non=inn["non"],
                             nxt=inn["next"], bf=bf, target=inn["target"], over_runs=inn["over"]["runs"],
                             bowl_start_over=1, style_shift=shift)
        if r >= inn["target"]:
            wins += 1
        elif r == inn["target"] - 1:
            ties += 1
    return wins * 2 + ties        # ties count half a win each (x2 scale)


def estimate(g: dict, n: int = N_SIMS) -> dict:
    """Win probability for the USER (and the computer) in percent.

    Returns {"user": float, "ai": float, "proj": int|None, "range": [lo, hi]|None,
             "curve": {...}}  - the caller stores 'curve' back into the game so the
    chase curves are computed once per match.
    """
    n = max(60, min(N_SIMS, int(n)))
    stage = g["stage"]
    if stage == "done" and g.get("result"):
        w = g["result"]["winner"]
        user = 100.0 if w == "user" else (50.0 if w == "tie" else 0.0)
        return {"user": user, "ai": 100.0 - user, "proj": None, "range": None, "curve": g["wp"].get("curve")}
    store = g["wp"].setdefault("curve", {})
    inn = g["inn"][g["n"]] if g["inn"] and g["n"] >= 0 else None

    def curve_for(chaser: str):
        if chaser not in store:
            store[chaser] = _chase_curve(g, chaser)
        return store[chaser]

    proj = None
    rng_ = None
    if inn is not None and inn["idx"] == 1:
        # second innings: direct rollouts from the live state
        wins2 = _chase_wins(g, inn, n)
        p_chaser = wins2 / (2.0 * n)
        user = p_chaser if inn["bat"] == "user" else 1.0 - p_chaser
    elif g["stage"] == "break" and inn is not None and inn["idx"] == 0:
        chaser = inn["bowl"]
        p_chaser = _curve_at(curve_for(chaser), inn["runs"] + 1)
        user = p_chaser if chaser == "user" else 1.0 - p_chaser
    elif inn is not None and inn["idx"] == 0:
        first, chaser = inn["bat"], inn["bowl"]
        totals = _innings_samples(g, first, inn, n)
        curve = curve_for(chaser)
        p_chaser = sum(_curve_at(curve, t + 1) for t in totals) / len(totals)
        user = p_chaser if chaser == "user" else 1.0 - p_chaser
        totals.sort()
        proj = int(round(sum(totals) / len(totals)))
        rng_ = [totals[int(len(totals) * 0.12)], totals[int(len(totals) * 0.88) - 1]]
    else:
        # before the toss / before the first ball: average both batting orders
        probs = []
        for first, chaser in (("user", "ai"), ("ai", "user")):
            totals = _innings_samples(g, first, None, max(60, n // 2))
            curve = curve_for(chaser)
            p_chaser = sum(_curve_at(curve, t + 1) for t in totals) / len(totals)
            probs.append(p_chaser if chaser == "user" else 1.0 - p_chaser)
        user = sum(probs) / 2.0
        if g.get("toss") and g["toss"].get("decision"):
            pass
    user = max(0.02, min(0.98, user))
    return {"user": round(user * 100.0, 1), "ai": round((1.0 - user) * 100.0, 1), "proj": proj, "range": rng_,
            "curve": store}


def record_point(g: dict, est: dict) -> None:
    """Append this estimate to the match's win-probability timeline."""
    wp = g["wp"]
    wp["user"] = est["user"]
    if est.get("proj") is not None:
        wp["proj"] = est["proj"]
        wp["range"] = est.get("range")
    else:
        wp["proj"] = None
        wp["range"] = None
    series = wp.setdefault("series", [])
    x = int(g.get("balls_played") or 0)
    if series and series[-1][0] == x:
        series[-1][1] = est["user"]
    else:
        series.append([x, est["user"]])
    if len(series) > 300:
        # thin the older half, keep the recent detail
        head = series[: len(series) - 150]
        wp["series"] = head[::2] + series[len(series) - 150:]
