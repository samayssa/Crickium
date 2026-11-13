"""Crickium commentary studio.

Builds a full, real-commentator style line for every ball in every game mode.
Public API:
    play_ball_commentary(session, context, outcome, striker_before, partnership_players=None, mode="play")
    so_ball_commentary(...)            Super Over
    render_ball(ctx)                   low level (used by the Mini App adapter)
    catalog_size()
"""
from __future__ import annotations

import random
import re
from collections import OrderedDict, deque
from typing import Any

from engines import commentary_banks as B

MAX_LEN = 300                                                                             
STORE_MAX = 80
THRILLER_RRR = 10.5                                                                 
TENSE_RRR = 8.5
THRILLER_WIH = 3                                                                                      
TENSE_WIH_CHASE = 5
TENSE_WIH_FIRST = 4


                                                                              
def surname(name: str) -> str:
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    if not parts:
        return "Player"
    if len(parts) > 1 and parts[-1].lower().rstrip(".") in ("jr", "sr", "ii", "iii"):
        return parts[-2]
    return parts[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _clean(name: Any) -> str:
    return re.sub(r"[†*()]", "", str(name or "")).strip()


class _Mem:
    def __init__(self) -> None:
        self.used: dict[str, deque] = {}
        self.zones: deque = deque(maxlen=3)
        self.since_sit = 99
        self.phase_done: set = set()
        self.last_wkt_bowler: str = ""


_STORE: "OrderedDict[Any, _Mem]" = OrderedDict()


def _mem_for(key: Any) -> _Mem:
    m = _STORE.get(key)
    if m is None:
        m = _STORE[key] = _Mem()
        while len(_STORE) > STORE_MAX:
            _STORE.popitem(last=False)
    else:
        _STORE.move_to_end(key)
    return m


def _pick(bank_id: str, items: list, rnd: random.Random, mem: _Mem):
    """Random item that was not used in the last few draws from the same bank."""
    if not items:
        return ""
    dq = mem.used.setdefault(bank_id, deque(maxlen=max(1, min(8, len(items) // 2))))
    fresh = [x for x in items if x not in dq] or items
    choice = rnd.choice(fresh)
    dq.append(choice)
    return choice


def _wchoice(rnd: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rnd.choices(keys, weights=[max(0.01, weights[k]) for k in keys], k=1)[0]


                                                                               
class Ctx:
    def __init__(self, **kw: Any) -> None:
        self.mode = "play"
        self.gender = "m"
        self.kind = "dot"
        self.runs = 0                                                   
        self.free_hit = False
        self.batter = ""
        self.non_striker = ""
        self.bowler = ""
        self.fielder = ""
        self.keeper = ""                                                               
        self.keepers: list[str] = []                                    
        self.roster: list[str] = []
        self.wk_kind = ""
        self.wk_mode = ""
        self.bat_runs = 0                                            
        self.bat_balls = 0
        self.bat_runs_ball = 0
        self.score = 0
        self.wkts = 0
        self.legal = 0                                                                  
        self.legal_ball = True
        self.target: int | None = None
        self.max_balls = 120
        self.max_wkts = 10
        self.over_no = 1
        self.ball_in_over = 1
        self.this_over: list[str] = []
        self.tactic = ""
        self.family = "p"
        self.pitch = ""
        self.fallen: list[dict] = []
        self.part_runs = 0
        self.team = ""
        self.opp = ""
        self.innings_over = False
        self.zone_hint = ""
        self.bowler_wkts: int | None = None                                                                                        
        self.no_ending = False
        self.stats_known = True                                                                              
        self.key: Any = None
        for k, v in kw.items():
            setattr(self, k, v)


                                                                               
def situation(c: Ctx) -> dict[str, Any]:
    wih = max(0, c.max_wkts - c.wkts)
    balls_left = max(0, c.max_balls - c.legal)
    chase = c.target is not None
    need = max(0, (c.target or 0) - c.score) if chase else 0
    rrr = (need * 6.0 / balls_left) if (chase and balls_left) else 0.0
    return {"wih": wih, "balls_left": balls_left, "chase": chase, "need": need, "rrr": rrr}


def pick_tone(c: Ctx, s: dict[str, Any]) -> str:
    if c.mode == "so":
        return "thriller"
    wih, bl = s["wih"], s["balls_left"]
    if s["chase"]:
        if s["need"] <= 0:
            return "calm"
        if wih <= THRILLER_WIH or (s["rrr"] >= THRILLER_RRR and bl <= 60) or (bl <= 6 and s["need"] <= 20):
            return "thriller"
        if wih <= TENSE_WIH_CHASE or s["rrr"] >= TENSE_RRR or (bl <= 24 and s["need"] > 0):
            return "tense"
        return "calm"
    if wih <= THRILLER_WIH:
        return "thriller"
    if wih <= TENSE_WIH_FIRST or (c.wkts >= 3 and c.legal <= 36):
        return "tense"
    return "calm"


def _ovl(balls: int) -> str:
    o, b = divmod(balls, 6)
    return _plural(o, "over") if not b else f"{o}.{b} overs"


def _sit_text(c: Ctx, s: dict[str, Any], tone: str, rnd: random.Random, mem: _Mem) -> str:
    bl, wih, need = s["balls_left"], s["wih"], s["need"]
    slots = {"need": str(need), "needw": _plural(need, "run"), "balls": str(bl), "ballsw": _plural(bl, "ball"),
             "rrr": f"{s['rrr']:.1f}", "wihw": _plural(wih, "wicket"), "score": str(c.score), "wkts": str(c.wkts),
             "target": str(c.target or ""), "ovl": _ovl(bl), "legal": str(c.legal)}
    if c.mode == "so":
        pool = B.SIT_SO if s["chase"] else B.SIT_SO_FIRST
    elif s["chase"]:
        if need <= 0:
            return ""
        pool = B.SIT_LAST_BALL if bl == 1 else B.SIT_LAST_OVER if bl <= 6 else B.SIT_CHASE_WICKETS if wih <= 3 else B.SIT_CHASE
    else:
        pool = B.SIT_FIRST_THRILLER if tone == "thriller" else B.SIT_FIRST_TENSE
    t = _pick("sit", pool, rnd, mem)
    return re.sub(r"\{(\w+)\}", lambda m: slots.get(m.group(1), m.group(0)), t)


                                                                               
def _pron(g: str) -> dict[str, str]:
    return ({"he": "she", "He": "She", "his": "her", "His": "Her", "him": "her"} if g == "f"
            else {"he": "he", "He": "He", "his": "his", "His": "His", "him": "him"})


def _slots(c: Ctx, **extra: str) -> dict[str, str]:
    sl = {"B": c.batter, "b": surname(c.batter), "W": c.bowler, "w": surname(c.bowler), "F": c.fielder, "f": surname(c.fielder),
          "K": c.keeper or (c.keepers[0] if c.keepers else "the keeper"), "k": surname(c.keeper or (c.keepers[0] if c.keepers else "keeper")),
          "n": surname(c.non_striker), "runs": str(c.bat_runs), "balls": str(c.bat_balls), "score": str(c.score), "pos": "", "air": "",
          "p": str(c.part_runs), "orun": "", "orunw": ""}
    sl.update(_pron(c.gender))
    sl.update(extra)
    return sl


def _fill(t: str, sl: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: sl.get(m.group(1), m.group(0)), t)


                                                                              
def _delivery(c: Ctx, rnd: random.Random, mem: _Mem) -> str:
    if c.family == "s":
        bank = B.DELIVERY_SPIN.get(c.tactic) or B.DELIVERY_GENERIC["s"]
    else:
        bank = B.DELIVERY_PACE.get(c.tactic) or B.DELIVERY_GENERIC["p"]
    return _pick("dl_" + (c.tactic or c.family), bank, rnd, mem)


def _zone(c: Ctx, base: dict[str, float], rnd: random.Random, mem: _Mem) -> str:
    hint = getattr(c, "zone_hint", "")
    if hint in base and rnd.random() < 0.85:
        mem.zones.append(hint)
        return hint
    w = dict(base)
    tilt = B.ZONE_TILT.get("spin" if c.family == "s" else c.tactic, {})
    for z, m in tilt.items():
        if z in w:
            w[z] *= m
    for z in mem.zones:                                                               
        if z in w:
            w[z] *= 0.45
    z = _wchoice(rnd, w)
    mem.zones.append(z)
    return z


def _fielder_for(c: Ctx, rnd: random.Random, exclude_keeper: bool = True) -> str:
    pool = [n for n in c.roster if surname(n) != surname(c.bowler)
            and not (exclude_keeper and any(surname(k) == surname(n) for k in c.keepers))]
    return rnd.choice(pool) if pool else (c.bowler or "the fielder")


def _opener(c: Ctx, tone: str, rnd: random.Random, mem: _Mem) -> str:
    k = c.kind
    if c.free_hit:
        if k == "four":
            return _pick("o_fh4", B.OPEN_FH_FOUR, rnd, mem)
        if k == "six":
            return _pick("o_fh6", B.OPEN_FH_SIX, rnd, mem)
        if k == "dot":
            return _pick("o_fhd", B.OPEN_FH_DOT, rnd, mem)
        return _pick("o_fhr", B.OPEN_FH_RUN, rnd, mem)
    bank = {"dot": B.OPEN_DOT, "single": B.OPEN_SINGLE, "double": B.OPEN_DOUBLE, "triple": B.OPEN_TRIPLE, "four": B.OPEN_FOUR,
            "six": B.OPEN_SIX, "five": B.OPEN_FIVE, "wicket": B.OPEN_WICKET}.get(k)
    return _pick(f"o_{k}_{tone}", bank[tone], rnd, mem) if bank else ""


def _main_runs(c: Ctx, tone: str, rnd: random.Random, mem: _Mem, opener: str = "") -> tuple[str, dict[str, Any]]:
    k = c.kind
    info: dict[str, Any] = {}
    if k in ("four", "six"):
        zone = _zone(c, B.ZONE_W_FOUR if k == "four" else B.ZONE_W_SIX, rnd, mem)
        shot = _pick(f"sh_{k}_{zone}", (B.SHOT_FOUR if k == "four" else B.SHOT_SIX)[zone], rnd, mem)
        body = f"{c.batter} {shot}."
    else:
        zone = _zone(c, B.ZONE_W_RUN, rnd, mem)
        shot = _pick(f"ss_{zone}", B.SHOT_SOFT[zone], rnd, mem)
        bank = {"single": B.RUN_1, "double": B.RUN_2, "triple": B.RUN_3, "five": B.RUN_3}.get(k, B.RUN_1)
        low = opener.lower()
        bad = [w for w in ("single", "one", "couple", "two", "three") if w in low]                        
        bank = [x for x in bank if not any(re.search(rf"\b{w}\b", x) for w in bad)] or bank
        run = _pick(f"r_{k}", bank, rnd, mem)
        body = f"{c.batter} {shot} {run}."
    if rnd.random() < 0.5:
        body = f"{_delivery(c, rnd, mem)}, {body}"
    info["zone"] = zone
    return body, info


def _main_dot(c: Ctx, tone: str, rnd: random.Random, mem: _Mem) -> tuple[str, dict[str, Any]]:
    kinds = {"beaten": 22, "defended": 25, "left": 12, "fielded": 18, "pad": 7, "edge": 6}
    t = _wchoice(rnd, kinds)
    info: dict[str, Any] = {}
    if t == "fielded":
        zone = rnd.choice(["cv", "pt", "mo", "mn", "mw", "sq"])
        f = _fielder_for(c, rnd)
        phrase = _pick("df", B.DOT_FIELDED, rnd, mem).replace("{f}", surname(f)).replace("{pos}", B.ZONES[zone][1])
        return f"{c.batter} {phrase}.", {"zone": zone}
    bank = {"beaten": B.DOT_BEATEN, "defended": B.DOT_DEFENDED, "left": B.DOT_LEFT, "pad": B.DOT_PAD, "edge": B.DOT_EDGE}[t]
    phrase = _pick("d_" + t, bank, rnd, mem)
    if t in ("beaten", "edge") and rnd.random() < 0.55:
        return f"{_delivery(c, rnd, mem)}, {c.batter} {phrase}.", info
    return f"{c.batter} {phrase}.", info


def _main_extras(c: Ctx, rnd: random.Random, mem: _Mem) -> str:
    k = c.kind
    if k == "wide":
        return _pick("wd", B.WIDE_FH if c.free_hit else B.WIDE, rnd, mem)
    if k == "no_ball":
        return _pick("nb", B.NO_BALL_FH if c.free_hit else B.NO_BALL, rnd, mem)
    if k == "bye":
        return _pick("by", B.BYE, rnd, mem)
    return _pick("lb", B.LEG_BYE, rnd, mem)


def _main_wicket(c: Ctx, rnd: random.Random, mem: _Mem) -> tuple[str, str]:
    """-> (text, wicket_type)"""
    kind = (c.wk_kind or "caught").lower()
    fielder_is_keeper = any(surname(k) == surname(c.fielder) for k in c.keepers) if c.fielder else False
    if kind == "caught":
        if c.fielder and surname(c.fielder) == surname(c.bowler):
            return _pick("w_cb", B.CAUGHT_BOWLED, rnd, mem), "caught_bowled"
        if fielder_is_keeper:
            c.keeper = c.fielder
            return _pick("w_cbh", B.CAUGHT_BEHIND, rnd, mem), "caught_behind"
        zone = _zone(c, B.ZONE_W_CATCH, rnd, mem)
        _, near, deep = B.ZONES[zone]
        if zone == "st":
            pos = rnd.choice(["long-on", "long-off", "mid-on", "mid-off"])
        else:
            pos = deep if rnd.random() < 0.55 else near
        return _pick("w_c", B.CAUGHT, rnd, mem).replace("{pos}", pos).replace("{air}", _pick("air", B.AIR, rnd, mem)), "caught"
    if kind == "stumped":
        c.keeper = c.fielder or (c.keepers[0] if c.keepers else "")
        return _pick("w_st", B.STUMPED, rnd, mem), "stumped"
    if kind == "lbw":
        return _pick("w_lbw", B.LBW_SPIN if c.family == "s" and rnd.random() < 0.4 else B.LBW, rnd, mem), "lbw"
    if kind == "run_out":
        zone = _zone(c, B.ZONE_W_CATCH, rnd, mem)
        pos = B.ZONES[zone][2] if zone != "st" else "mid-wicket"
        if c.wk_mode == "relay" and c.keeper:
            return _pick("w_ror", B.RUN_OUT_RELAY, rnd, mem).replace("{pos}", pos), "run_out"
        return _pick("w_rod", B.RUN_OUT_DIRECT, rnd, mem).replace("{pos}", pos), "run_out"
    return _pick("w_b", B.BOWLED, rnd, mem), "bowled"


def _wicket_extras(c: Ctx, wtype: str, tone: str, rnd: random.Random, mem: _Mem) -> list[str]:
    out: list[str] = []
    runs, balls = c.bat_runs, c.bat_balls
    if not c.stats_known:
        pass
    elif runs == 0 and balls == 1:
        out.append(_pick("wk_g", B.WK_GOLDEN, rnd, mem))
    elif runs == 0:
        out.append(_pick("wk_d", B.WK_DUCK, rnd, mem))
    elif runs >= 30:
        out.append(_pick("wk_big", B.WK_BIG, rnd, mem))
    elif runs >= 15:
        out.append(_pick("wk_good", B.WK_GOOD, rnd, mem))
    else:
        out.append(_pick("wk_t", B.WK_TAILS, rnd, mem))
    credited = [w for w in c.fallen if str(w.get("type") or "") not in ("run_out",) and surname(str(w.get("bowler") or "")) == surname(c.bowler)]
    n = len(credited) if wtype != "run_out" else 0
    if c.bowler_wkts is not None:
        n = c.bowler_wkts if wtype != "run_out" else 0
    if n in B.WK_BOWLER and (c.bowler_wkts is None or n == 2) and rnd.random() < 0.85:
        out.append(_pick(f"wkb{n}", B.WK_BOWLER[n], rnd, mem))
    if wtype != "run_out" and len(c.fallen) >= 3:                                                           
        def idx(w):
            o, _, b = str(w.get("over") or "0.0").partition(".")
            return int(o or 0) * 6 + int(b or 0)
        last = c.fallen[-3:]
        if all(w.get("bowler") and surname(str(w["bowler"])) == surname(c.bowler) and str(w.get("type")) != "run_out" for w in last) \
                and idx(last[2]) - idx(last[1]) == 1 and idx(last[1]) - idx(last[0]) == 1:
            out.insert(0, _pick("ht", B.HAT_TRICK, rnd, mem))
    if c.part_runs >= 25 and rnd.random() < 0.6:
        out.append(_pick("wk_p", B.WK_PARTNERSHIP, rnd, mem))
    return out


                                                                               
def render_ball(c: Ctx, rnd: random.Random | None = None) -> str:
    rnd = rnd or random.Random()
    mem = _mem_for(c.key)
    s = situation(c)
    tone = pick_tone(c, s)
    sl_parts: list[str] = []                
    opt: list[str] = []                                                        
    k = c.kind
    opener = _opener(c, tone, rnd, mem) if k not in ("wide", "no_ball", "bye", "leg_bye") else ""
    info: dict[str, Any] = {}
    wtype = ""
    if k == "wicket":
        main, wtype = _main_wicket(c, rnd, mem)
        sl_parts = [opener, _fill(main, _slots(c))]
        if c.free_hit and wtype == "run_out":
            sl_parts.append(B.RUN_OUT_RARE)
        opt += _wicket_extras(c, wtype, tone, rnd, mem)[:3]
    elif k in ("wide", "no_ball", "bye", "leg_bye"):
        sl_parts = [_fill(_main_extras(c, rnd, mem), _slots(c))]
        if k == "wide" and c.free_hit is False and rnd.random() < 0.0:
            pass
    elif k == "dot":
        main, info = _main_dot(c, tone, rnd, mem)
        sl_parts = [opener, _fill(main, _slots(c))]
        if rnd.random() < (0.55 if tone == "thriller" else 0.22):
            opt.append(_pick("t_dot", B.TAIL_THRILLER["dot"] if tone == "thriller" else B.TAIL_DOT, rnd, mem))
    else:
        main, info = _main_runs(c, tone, rnd, mem, opener)
        sl_parts = [opener, _fill(main, _slots(c))]
        tails = {"single": B.TAIL_SINGLE, "double": B.TAIL_DOUBLE, "triple": B.TAIL_DOUBLE, "five": B.TAIL_DOUBLE, "four": B.TAIL_FOUR, "six": B.TAIL_SIX}[k]
        if tone == "thriller" and k in B.TAIL_THRILLER and rnd.random() < 0.55:
            tails = B.TAIL_THRILLER[k]
        if rnd.random() < {"single": 0.3, "double": 0.3, "triple": 0.5, "five": 0.5, "four": 0.5, "six": 0.5}[k] or (tone == "thriller" and rnd.random() < 0.3):
            opt.append(_pick(f"t_{k}_{tone}", tails, rnd, mem))
                                                                               
        zone = info.get("zone")
        if zone and rnd.random() < {"four": 0.28, "six": 0.18, "single": 0.1, "double": 0.12}.get(k, 0.0):
            f = _fielder_for(c, rnd)
            pos = B.ZONES[zone][2] if k in ("four", "six") else B.ZONES[zone][1]
            bank = {"four": B.CAMEO_FOUR, "six": B.CAMEO_SIX, "single": B.CAMEO_SINGLE, "double": B.CAMEO_DOUBLE}[k]
            opt.append(_fill(_pick(f"cam_{k}", bank, rnd, mem), {"f": surname(f), "pos": pos, "his": _pron(c.gender)["his"]}))
                                                                              
    over_syms = list(c.this_over or [])
    dots = 0
    for sym in reversed(over_syms):
        if sym == "0":
            dots += 1
        else:
            break
    w = surname(c.bowler)
    if k == "dot" and dots >= 2 and rnd.random() < 0.6:
        opt.insert(0, _fill(_pick("ds", B.DOT_STREAK[min(5, dots + 1)], rnd, mem), {"w": w}))
    if k in ("four", "six") and dots >= 3 and rnd.random() < 0.6:
        opt.insert(0, _pick("bad", B.BOUNDARY_AFTER_DOTS, rnd, mem))
    elif k in ("four", "six") and over_syms and over_syms[-1] in ("4", "6") and rnd.random() < 0.5:
        nb = sum(1 for x in over_syms if x in ("4", "6")) + 1
        opt.insert(0, _fill(_pick("b3" if nb >= 3 else "bb", B.THREE_BOUNDARIES if nb >= 3 else B.BACK_TO_BACK, rnd, mem), {"w": w}))
    if k in ("four", "six", "single", "double", "triple", "five") and c.bat_runs:
        r0 = c.bat_runs - int(getattr(c, "bat_runs_ball", 0) or 0)
        if c.bat_runs >= 100 > r0:
            sl_parts.append(_fill(_pick("m100", B.MILE_HUNDRED, rnd, mem), _slots(c)))
        elif c.bat_runs >= 50 > r0:
            sl_parts.append(_fill(_pick("m50", B.MILE_FIFTY, rnd, mem), _slots(c)))
        elif c.bat_runs >= 90 > r0 and tone != "calm":
            opt.append(_fill(_pick("m90", B.MILE_NINETIES, rnd, mem), _slots(c)))
                                                                                   
    if c.legal_ball and c.ball_in_over == 6 and not c.innings_over and c.mode != "so" and (len(over_syms) + 1) >= 6:
        syms = over_syms + [{"dot": "0", "single": "1", "double": "2", "triple": "3", "four": "4", "six": "6", "wicket": "W"}.get(k, "x")]
        if all(x in ("0", "1", "2", "3", "4", "6", "W") for x in syms):
            orun = sum(int(x) for x in syms if x.isdigit())
            nw = syms.count("W")
            sl = {"W": c.bowler, "orun": str(orun), "orunw": _plural(orun, "run")}
            if nw >= 3:
                opt.append(_fill(_pick("ow3", B.OVER_WICKETS_3, rnd, mem), sl))
            elif nw == 2:
                opt.append(_fill(_pick("ow2", B.OVER_WICKETS_2, rnd, mem), sl))
            elif nw == 1 and orun <= 8:
                opt.append(_fill(_pick("ow1", B.OVER_WICKET, rnd, mem), sl))
            elif orun == 0:
                opt.append(_fill(_pick("mai", B.MAIDEN, rnd, mem), sl))
            elif orun >= 14:
                opt.append(_fill(_pick("oex", B.OVER_EXPENSIVE, rnd, mem), sl))
            elif orun <= 4:
                opt.append(_fill(_pick("oti", B.OVER_TIGHT, rnd, mem), sl))
                                                                                   
    if tone == "calm" and c.legal_ball and c.ball_in_over == 1 and c.mode not in ("so",):
        ph = {1: B.PHASE_PP, 7: B.PHASE_MID, 16: B.PHASE_DEATH}.get(c.over_no)
        if ph and ("ph", c.over_no) not in mem.phase_done and rnd.random() < 0.6:
            mem.phase_done.add(("ph", c.over_no))
            opt.append(_pick("ph", ph, rnd, mem))
        elif c.over_no == 1 and c.pitch and rnd.random() < 0.3:
            notes = B.PITCH_NOTES.get(str(c.pitch).lower())
            if notes:
                opt.append(_pick("pn", notes, rnd, mem))
                                                                                     
    use_sit = False
    if tone == "thriller":
        use_sit = rnd.random() < (0.9 if (s["balls_left"] <= 12 or k == "wicket") else 0.75 if mem.since_sit >= 1 else 0.25)
    elif tone == "tense":
        use_sit = rnd.random() < (0.75 if mem.since_sit >= 3 else 0.1)
    mem.since_sit = 0 if use_sit else mem.since_sit + 1
    sit = _sit_text(c, s, tone, rnd, mem) if use_sit else ""
                                                                                    
    ending = ""
    if c.no_ending:
        pass
    elif c.target is not None and s["need"] <= 0 and c.mode != "so":
        ending = f"{c.team} win it by {_plural(s['wih'], 'wicket')}!" if c.team else "The chase is complete!"
    elif c.innings_over and c.target is not None:
        short = max(0, (c.target or 0) - 1 - c.score)
        ending = (f"The chase ends {short} runs short, and {c.opp} win it." if short else f"It is a tie at {c.score}!") if c.opp else ""
    elif c.innings_over and k == "wicket" and c.wkts >= c.max_wkts - 0 and c.mode != "so":
        opt.append(_fill(_pick("ao", B.ALL_OUT, rnd, mem), {"score": str(c.score)}))
                                                                                       
    mand = [x for x in sl_parts if x]
    pieces = mand + opt + ([sit] if sit else []) + ([ending] if ending else [])
    text = _join(pieces)
    while len(text) > MAX_LEN and len(pieces) > len(mand):
                                                         
        for i in range(len(pieces) - 1, len(mand) - 1, -1):
            if pieces[i] != ending:
                del pieces[i]
                break
        text = _join(pieces)
    return _tidy(_normalize_names(_fill(text, _slots(c)), c))


def _join(parts: list[str]) -> str:
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p[-1] not in ".!?":
            p += "."
        out.append(p)
    return " ".join(out)


_AN = re.compile(r"\b([Aa]) (?=(?:8\d{0,2}|11|18)(?!\d))")
_FIX = [(re.compile(r"(?<![\d.])1 (wicket|ball|run|over)s\b"), r"1 \1"), (re.compile(r"\blast 1 wicket\b"), "last wicket"),
        (re.compile(r"(?<![\d.])(1 (?:wicket|ball|run|over)) remain\b"), r"\1 remains")]


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"([.!?])\1{1,}", r"\1", text)
    text = re.sub(r"\.\s*([!?])", r"\1", text)
    for rx, to in _FIX:
        text = rx.sub(to, text)
    text = _AN.sub(lambda m: "An " if m.group(1) == "A" else "an ", text)
    return text[:1].upper() + text[1:] if text else text


def _normalize_names(text: str, c: Ctx) -> str:
    """Full name once per line, surname afterwards."""
    for full in {c.batter, c.bowler, c.fielder, c.keeper, c.non_striker}:
        full = (full or "").strip()
        if not full or " " not in full:
            continue
        first = text.find(full)
        if first < 0:
            continue
        head, tail = text[: first + len(full)], text[first + len(full):]
        text = head + tail.replace(full, surname(full))
    return text


def trim_to(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: m + 1] if m > limit * 0.5 else cut.rstrip() + "…"


                                                                              
def _is_keeper(p: dict) -> bool:
    return "wicket" in str(p.get("role") or "").lower() or "keeper" in str(p.get("role") or "").lower()


def _family(session: Any, tactic: str) -> str:
    spin = {"off_break", "doosra", "arm_ball", "carrom_ball", "top_spin", "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball"}
    if tactic in spin:
        return "s"
    hand = str((getattr(session, "current_bowler", None) or {}).get("bowling_hand") or "")
    return "s" if hand in ("RAO", "LAO", "RAL", "LAL") and tactic not in B.DELIVERY_PACE else "p"


def play_ball_commentary(session: Any, context: Any, outcome: Any, striker_before: Any,
                         partnership_players: tuple | None = None, mode: str = "play",
                         rng: random.Random | None = None) -> str:
    inn = session.innings
    score = inn.score
    bowler = getattr(session, "current_bowler", None) or {}
    xi = list(getattr(session, "bowling_squad", None) or [])[:11]
    kind = str(outcome.outcome or "dot")
    keepers = [_clean(p.get("name")) for p in xi if isinstance(p, dict) and _is_keeper(p)]
    wicket = bool(outcome.wicket)
    tactic = str(getattr(context, "bowler_tactic", "") or getattr(session, "current_tactic", "") or "")
    c = Ctx(
        mode=mode, gender="f" if mode == "wpl" else "m", kind="wicket" if wicket else kind, runs=int(outcome.runs or 0),
        free_hit=bool(getattr(context, "free_hit_next_ball", False)),
        batter=_clean(striker_before.name if striker_before else "The batter"),
        non_striker=_clean(partnership_players[1] if partnership_players and len(partnership_players) > 1 else ""),
        bowler=_clean(bowler.get("name") or "The bowler"), fielder=_clean(getattr(outcome, "fielder", "") or ""),
        keeper=_clean(getattr(outcome, "assist", "") or ""), keepers=keepers, roster=[_clean(p.get("name")) for p in xi if isinstance(p, dict)],
        wk_kind=str(getattr(outcome, "wicket_kind", "") or ""), wk_mode=str(getattr(outcome, "dismissal_mode", "") or ""),
        bat_runs=int(getattr(striker_before, "runs", 0) or 0), bat_balls=int(getattr(striker_before, "balls", 0) or 0),
        bat_runs_ball=int(getattr(outcome, "batter_runs", 0) or 0),
        score=int(score.runs), wkts=int(score.wickets), legal=int(score.legal_balls), legal_ball=bool(outcome.legal),
        target=getattr(inn, "target", None), max_balls=120, max_wkts=10,
        over_no=int(getattr(context, "over_number", 1) or 1), ball_in_over=int(getattr(context, "ball_number", 1) or 1),
        this_over=list(getattr(session, "this_over", []) or []), tactic=tactic, family=_family(session, tactic),
        pitch=str(getattr(session, "pitch", "") or ""), fallen=list(score.wickets_fallen or []),
        part_runs=int(getattr(session, "partnership_runs", 0) or 0),
        team=str(getattr(session, "batting_team_display", "") or ""), opp=str(getattr(session, "bowling_team_display", "") or ""),
        innings_over=bool(score.legal_balls >= 120 or score.wickets >= 10),
        key=(mode, getattr(session, "match_id", None), getattr(session, "chat_id", None), getattr(inn, "innings_number", 1)),
    )
    return render_ball(c, rng)


def catalog_size() -> int:
    """Phrase count in the banks (distinct lines = product of these, far larger)."""
    n = 0
    for name in dir(B):
        v = getattr(B, name)
        if name.isupper() and not name.startswith("ZONE"):
            n += _count(v)
    return n


def _count(v: Any) -> int:
    if isinstance(v, str):
        return 1
    if isinstance(v, (list, tuple)):
        return sum(_count(x) for x in v)
    if isinstance(v, dict):
        return sum(_count(x) for x in v.values())
    return 0


                                                                              
_SHOT_ZONES = (("scoop", "fl"), ("ramp", "fl"), ("glance", "fl"), ("flick", "sq"), ("sweep", "sq"), ("pull", "mw"), ("hook", "sq"),
               ("slog", "mw"), ("on drive", "mn"), ("on-drive", "mn"), ("off drive", "mo"), ("off-drive", "mo"), ("cover", "cv"),
               ("square cut", "pt"), ("cut", "pt"), ("late", "tm"), ("steer", "tm"), ("loft", "st"), ("straight", "st"), ("lofted", "st"),
               ("defen", ""), ("block", ""), ("leave", ""))


def zone_from_shot(shot: str) -> str:
    low = str(shot or "").lower()
    for key, z in _SHOT_ZONES:
        if key in low:
            return z
    return ""


def so_ball_commentary(state: dict, result: Any, batter: dict, bowler: dict, non_striker: dict | None = None,
                       dismissal: dict | None = None, shot: str = "", kind: str = "dot",
                       rng: random.Random | None = None) -> str:
    """Super Over line.  `state` is the SO match state AFTER the ball was added to runs/wickets."""
    xi = [p for p in (state.get("bowling_xi") or []) if isinstance(p, dict)]
    bs = (state.get("batter_stats") or {}).get(str(batter.get("player_id"))) or {}
    runs_ball = int(getattr(result, "runs", 0) or 0) if kind in ("single", "double", "triple", "four", "six", "five") else 0
    syms = ["0" if x in ("•", "·", ".") else str(x) for x in (state.get("this_over") or [])][:-1]                        
    d = dismissal or {}
    legal = int(state.get("legal_balls") or 0)
    c = Ctx(
        mode="so", kind=kind, runs=int(getattr(result, "runs", 0) or 0), batter=_clean(batter.get("name")), bowler=_clean(bowler.get("name")),
        non_striker=_clean((non_striker or {}).get("name")), fielder=_clean(d.get("fielder")), keeper=_clean(d.get("assist")),
        keepers=[_clean(p.get("name")) for p in xi if _is_keeper(p)], roster=[_clean(p.get("name")) for p in xi],
        wk_kind=str(d.get("kind") or ""), wk_mode=str(d.get("mode") or ""),
        bat_runs=int(bs.get("runs") or 0) + runs_ball, bat_balls=int(bs.get("balls") or 0) + (1 if getattr(result, "legal", True) else 0),
        score=int(state.get("runs") or 0), wkts=int(state.get("wickets") or 0), legal=legal, legal_ball=bool(getattr(result, "legal", True)),
        target=state.get("target"), max_balls=6, max_wkts=2, over_no=1, ball_in_over=max(1, min(6, legal)), this_over=syms,
        tactic=str(state.get("delivery") or ""), family="s" if str(state.get("family") or "").lower().startswith("spin") else "p",
        pitch="", fallen=list(state.get("wickets_fallen") or []), part_runs=int(state.get("partnership_runs") or 0),
        team="", opp="", innings_over=bool(legal >= 6 or int(state.get("wickets") or 0) >= 2),
        zone_hint=zone_from_shot(shot), key=("so", state.get("match_id"), state.get("innings_no")),
    )
    return render_ball(c, rng)


                                                                              
_SPIN_TACTICS = {"off_break", "doosra", "arm_ball", "carrom_ball", "top_spin", "leg_breaker", "top_spinner", "slider", "flipper", "googly_ball"}
_MA_KIND = {0: "dot", 1: "single", 2: "double", 3: "triple", 4: "four", 6: "six", "W": "wicket", "WD": "wide", "NB": "no_ball", "BY": "bye", "LB": "leg_bye"}


def miniapp_ball_commentary(g: dict, inn: dict, striker: dict, bowl_p: dict, outcome: Any, dismissal: dict | None,
                            tactic: str, free_hit: bool, fielders: list[dict], bstat: dict | None = None,
                            rng: random.Random | None = None) -> str:
    """Mini App "Play AI" line.  Called AFTER the ball has been applied to `inn`."""
    kind = _MA_KIND.get(outcome, "dot")
    d = dismissal or {}
    names = [_clean(p.get("name")) for p in fielders if isinstance(p, dict)]
    keepers = [_clean(p.get("name")) for p in fielders if isinstance(p, dict) and _is_keeper(p)]
    stk = _clean(striker.get("name"))
    other = ""
    try:
        for idx in (inn["striker"], inn["non"]):
            nm = _clean(inn["batters"][idx]["name"])
            if nm and nm != stk:
                other = nm
                break
    except Exception:
        pass
    syms = ["0" if x in ("•", "·", ".") else str(x) for x in (inn.get("over") or {}).get("balls", [])][:-1]
    overs_total = int(g["cfg"]["overs"])
    runs_ball = outcome if isinstance(outcome, int) else (0 if outcome == "W" else 1)
    legal_in_over = int((inn.get("over") or {}).get("legal", 0))
    c = Ctx(
        mode="miniapp", kind=kind, runs=runs_ball, free_hit=free_hit, batter=stk, non_striker=other, bowler=_clean(bowl_p.get("name")),
        fielder=_clean(d.get("fielder")), keeper=_clean(d.get("assist")), keepers=keepers, roster=names,
        wk_kind=str(d.get("kind") or ""), wk_mode=str(d.get("mode") or ""),
        bat_runs=int(striker.get("runs") or 0), bat_balls=int(striker.get("balls") or 0),
        bat_runs_ball=runs_ball if isinstance(outcome, int) and outcome in (1, 2, 3, 4, 6) else 0,
        score=int(inn.get("runs") or 0), wkts=int(inn.get("wkts") or 0), legal=int(inn.get("balls") or 0),
        legal_ball=outcome not in ("WD", "NB"), target=inn.get("target"), max_balls=overs_total * 6, max_wkts=10,
        over_no=int((inn.get("over") or {}).get("no") or 1), ball_in_over=max(1, legal_in_over), this_over=syms,
        tactic=str(tactic or ""), family="s" if str(tactic or "") in _SPIN_TACTICS else "p", pitch=str(g["cfg"].get("pitch") or ""),
        fallen=[], part_runs=int((inn.get("partner") or {}).get("runs") or 0),
        key=("miniapp", g.get("id") or g.get("game_id") or id(g), inn.get("idx")),
    )
    c.bowler_wkts = int((bstat or {}).get("wkts") or 0)
    c.no_ending = True
    c.innings_over = bool(c.legal >= c.max_balls or c.wkts >= 10)
                                                                                               
    return render_ball(c, rng)


                                                                               
_STAGE = {
    "challenge": ["A challenge is on the table.", "A new match has been invited, and the stage is set."],
    "toss": ["The coin is in the air.", "Toss time, and the match is balanced on a coin edge."],
    "decision": ["Bat or bowl? The next call will shape the game.", "A tactical decision is needed now."],
    "innings_start": ["The innings begins now, and the players take their marks.", "Play is underway."],
    "match_end": ["The match is over.", "A final result has been decided."],
}


def stage_banner(stage: str, challenger: str | None = None, opponent: str | None = None) -> str:
    lines = _STAGE.get((stage or "").strip().lower())
    base = random.choice(lines) if lines else str(stage or "").replace("_", " ").title()
    return f"{base} {challenger} vs {opponent}" if challenger and opponent else base


def over_summary(runs: int, wickets: int, overs_text: str) -> str:
    return f"After {overs_text} overs: {runs}/{wickets}"


def innings_summary(team_name: str | None, runs: int, wickets: int, overs_text: str, target: int | None = None) -> str:
    team = team_name or "Team"
    if target is None:
        return f"{team} finished on {runs}/{wickets} in {overs_text} overs."
    if runs >= target:
        return f"{team} chased the target with {runs}/{wickets} in {overs_text} overs."
    return f"{team} ended on {runs}/{wickets} in {overs_text} overs, target was {target}."


def match_result_message(winner_name: str | None, loser_name: str | None, margin_text: str) -> str:
    if winner_name and loser_name:
        return f"{winner_name} defeated {loser_name} by {margin_text}."
    if winner_name:
        return f"{winner_name} won the match by {margin_text}."
    return f"Match completed by {margin_text}."


def simple_ball_line(kind: str, batter: str, bowler: str, runs: int = 0) -> str:
    """Last-resort line if the studio itself ever fails (names only)."""
    b, w = batter or "The batter", surname(bowler or "the bowler")
    return {"four": f"FOUR! {b} finds the boundary off {w}.", "six": f"SIX! {b} clears the rope off {w}.",
            "wicket": f"OUT! {w} gets {b}.", "wide": f"Wide from {w}.", "no_ball": f"No ball from {w}, free hit next!",
            "bye": "Bye.", "leg_bye": "Leg bye."}.get(kind, f"{b} takes {runs} off {w}." if runs else f"Dot ball, {w} to {b}.")


_MATCH_KIND = {"dot": "dot", "single": "single", "double": "double", "triple": "triple", "four": "four", "boundary": "four", "five": "five",
               "six": "six", "wide": "wide", "no_ball": "no_ball", "bye": "bye", "leg_bye": "leg_bye", "mishit": "dot"}


def match_ball_commentary(session: Any, outcome: str, runs: int, batter: str, bowler: dict | None, dismissal: dict | None = None,
                          shot_name: str = "", delivery_name: str = "", fielding_xi: list | None = None,
                          rng: random.Random | None = None) -> str:
    """Normal (challenge) match line.  Called AFTER the ball was registered on `session`."""
    out = str(outcome or "dot").lower()
    wicket = out in ("out", "run_out")
    d = dismissal or {}
    xi = [p for p in (fielding_xi or []) if isinstance(p, dict)]
    score = session.score
    inn = getattr(session, "innings", None)
    meta = getattr(session, "meta", {}) or {}
    c = Ctx(
        mode="match", kind="wicket" if wicket else _MATCH_KIND.get(out, "dot"), runs=int(runs or 0), batter=_clean(batter), bowler=_clean((bowler or {}).get("name")),
        fielder=_clean(d.get("fielder")), keeper=_clean(d.get("assist")), keepers=[_clean(p.get("name")) for p in xi if _is_keeper(p)],
        roster=[_clean(p.get("name")) for p in xi], wk_kind="run_out" if out == "run_out" else str(d.get("kind") or ""), wk_mode=str(d.get("mode") or ""),
        bat_runs_ball=int(runs or 0) if out in ("single", "double", "triple", "four", "boundary", "five", "six") else 0,
        score=int(score.runs), wkts=int(score.wickets), legal=int(score.legal_balls), legal_ball=out not in ("wide", "no_ball"),
        target=getattr(inn, "target", None), max_balls=int(meta.get("max_overs") or 20) * 6, max_wkts=10,
        over_no=int(score.legal_balls // 6) + 1, ball_in_over=max(1, int(score.legal_balls % 6) or (6 if score.legal_balls else 1)),
        tactic=str(delivery_name or ""), family="s" if any(x in str(delivery_name or "").lower() for x in ("spin", "googly", "doosra", "break", "carrom", "flipper")) else "p",
        pitch=str(meta.get("pitch") or ""), zone_hint=zone_from_shot(shot_name), no_ending=True, stats_known=False,
        key=("match", getattr(session, "match_id", None) or id(session), getattr(inn, "innings_number", 1)),
    )
    c.innings_over = bool(c.legal >= c.max_balls or c.wkts >= 10)
    return render_ball(c, rng)
