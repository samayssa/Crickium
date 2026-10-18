"""Core match engine for the Mini App "Play Game" mode (user vs computer).

Pure, synchronous, JSON-serialisable.  The whole match lives in one dict
(``game``) so it can be cached in memory, stored as JSONB after every over and
rebuilt after a restart.  No database or network access happens here.

Rules mirror the bot's own engines (engines/play_runtime.py,
engines/innings_engine.py, engines/score_engine.py):

* batting order = Playing XI order, new batters start with 0 confidence
* wide / no-ball add 1 run and are not legal deliveries, a no-ball gives a
  free hit (no wicket), byes / leg-byes are not charged to the bowler
* odd runs change the strike (not on a wide / no-ball), the ends change after
  every over, a wicket on the last ball of an over puts the new batter at the
  non-striker's end
* run-outs are very rare (max one per innings) and are not credited to the
  bowler, a spinner can stump, a pace bowler never
* a bowler may bowl a fifth of the innings, never two overs in a row (unless
  he is the only one left)
* target = first innings runs + 1; level scores = tie (no super over)

Everything about probabilities comes from engines/miniapp_tactics_engine.py
and every decision of the computer from services/miniapp_game_ai.py.
"""

from __future__ import annotations

import copy
import random
import time
import uuid
from typing import Any

import engines.miniapp_tactics_engine as eng
import services.miniapp_game_ai as ai
import services.miniapp_game_commentary as com
from services.miniapp_game_squads import (
    AI_TEAMS, build_ai_squad, can_bowl, plain, team_code, user_team_name,
)

SCHEMA_VERSION = 1
OVERS_CHOICES = eng.SUPPORTED_OVERS
SIM_MODES = ("ball", "over")
PITCH_ORDER = ("even", "flat", "hard", "green", "bouncy", "dry", "dusty", "slow")

PITCH_INFO = {
    "even": {"label": "Even", "emoji": "⚖️", "desc": "Balanced for bat and ball"},
    "flat": {"label": "Flat", "emoji": "🛣️", "desc": "Batting paradise, big scores"},
    "hard": {"label": "Hard", "emoji": "🪨", "desc": "True bounce, strokes flow"},
    "green": {"label": "Green", "emoji": "🌿", "desc": "Seamers get movement"},
    "bouncy": {"label": "Bouncy", "emoji": "🏀", "desc": "Pace and bounce trouble batters"},
    "dry": {"label": "Dry", "emoji": "🏜️", "desc": "Spinners come into play"},
    "dusty": {"label": "Dusty", "emoji": "🌪️", "desc": "Turn and low bounce"},
    "slow": {"label": "Slow", "emoji": "🐢", "desc": "Hard to time the ball"},
}

MINDSET_META = {
    "defensive": {"label": "Defensive", "emoji": "🛡️", "hint": "Block, survive, protect the wicket"},
    "rotate": {"label": "Rotate", "emoji": "🔄", "hint": "Keep the strike moving, singles and twos"},
    "neutral": {"label": "Neutral", "emoji": "⚖️", "hint": "Balanced, play the ball on merit"},
    "aggressive": {"label": "Aggressive", "emoji": "⚔️", "hint": "Look for boundaries, accept risk"},
    "ultra_aggressive": {"label": "Ultra Aggressive", "emoji": "🚀", "hint": "Everything goes, big hits or bust"},
}

TACTIC_META = {
    "defensive": {"label": "Defensive", "emoji": "🛡️", "hint": "Tight lines, deny runs"},
    "swinging": {"label": "Swinging", "emoji": "🌀", "hint": "Late movement through the air"},
    "pace_up": {"label": "Pace Up", "emoji": "⚡", "hint": "Extra pace, rushes the batter"},
    "back_of_length": {"label": "Back Of Length", "emoji": "📏", "hint": "Awkward bounce, hard to free the arms"},
    "variation": {"label": "Variation", "emoji": "🎯", "hint": "Slower balls and yorkers, change the pace"},
    "off_break": {"label": "Off Break", "emoji": "🌀", "hint": "Classic turn into the right-hander"},
    "doosra": {"label": "Doosra", "emoji": "🔄", "hint": "Turns the other way"},
    "arm_ball": {"label": "Arm Ball", "emoji": "➡️", "hint": "Skids on straight"},
    "carrom_ball": {"label": "Carrom Ball", "emoji": "🎯", "hint": "Flicked out of the fingers, hard to read"},
    "top_spin": {"label": "Top Spin", "emoji": "⬆️", "hint": "Dips and bounces extra"},
    "leg_breaker": {"label": "Leg Breaker", "emoji": "🌀", "hint": "Big turn away from the bat"},
    "top_spinner": {"label": "Top Spinner", "emoji": "⬆️", "hint": "Bounces more than expected"},
    "slider": {"label": "Slider", "emoji": "↔️", "hint": "Skids low and straight"},
    "flipper": {"label": "Flipper", "emoji": "💨", "hint": "Zips low under the bat"},
    "googly_ball": {"label": "Googly", "emoji": "🔀", "hint": "The wrong 'un, turns into the bat"},
}

CHOICE_ALIASES = {"googly": "googly_ball", "ultra": "ultra_aggressive", "ultra aggressive": "ultra_aggressive"}

_SYMBOL = {0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 6: "6", "W": "W", "WD": "wd", "NB": "nb", "BY": "b", "LB": "lb"}
_NAME = {0: "dot", 1: "single", 2: "double", 3: "triple", 4: "four", 6: "six", "W": "wicket",
         "WD": "wide", "NB": "no_ball", "BY": "bye", "LB": "leg_bye"}
_CONF_NAME = {0: "dot", 1: "single", 2: "double", 3: "triple", 4: "four", 6: "six"}
MAX_FEED = 60
MAX_HIST = 40
MAX_WP_POINTS = 280


class GameError(Exception):
    """A rule violation that the API reports back as a 4xx message."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail or code


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def normalize_config(raw: dict | None, rng: random.Random | None = None) -> dict:
    raw = raw or {}
    rnd = rng or random
    difficulty = str(raw.get("difficulty") or "medium").strip().lower()
    if difficulty not in eng.DIFFICULTY_ORDER:
        raise GameError("bad_difficulty", "Choose Easy, Medium, Hard or Expert.")
    try:
        overs = int(raw.get("overs") or 0)
    except (TypeError, ValueError):
        overs = 0
    if overs not in OVERS_CHOICES:
        raise GameError("bad_overs", "Choose 2, 5, 10 or 20 overs.")
    sim = str(raw.get("sim") or "ball").strip().lower().replace("-", "_")
    sim = {"ball_by_ball": "ball", "over_by_over": "over", "bbb": "ball", "obo": "over"}.get(sim, sim)
    if sim not in SIM_MODES:
        raise GameError("bad_sim", "Choose ball by ball or over by over.")
    pitch = str(raw.get("pitch") or "even").strip().lower()
    random_pitch = pitch in {"random", "any", ""}
    if random_pitch:
        pitch = rnd.choice(PITCH_ORDER)
    if pitch not in PITCH_INFO:
        raise GameError("bad_pitch", "Pick one of the pitch types.")
    return {"difficulty": difficulty, "overs": overs, "sim": sim, "pitch": pitch,
            "random_pitch": bool(random_pitch)}


# ---------------------------------------------------------------------------
# creation and Playing XI
# ---------------------------------------------------------------------------
def _player_view(p: dict) -> dict:
    return {"pid": p["pid"], "eid": p.get("eid"), "kind": p.get("kind"), "name": p["name"],
            "role": p["role"], "bat": p["bat"], "bowl": p["bowl"], "bh": p.get("bh"), "bw": p.get("bw"),
            "upg": bool(p.get("ub") or p.get("uw"))}


def new_game(user_id: int, cfg: dict, *, first_name: str | None, franchise: str | None,
             squad: list[dict], lineup_pids: list[int], ai_pool: list[dict],
             rng: random.Random | None = None) -> dict:
    """Create a game in the Playing-XI stage.  `squad` holds compact players
    (services.miniapp_game_squads.make_player) in saved order."""
    rnd = rng or random.Random()
    by_pid = {int(p["pid"]): p for p in squad}
    xi: list[dict] = []
    seen: set[int] = set()
    for pid in lineup_pids or []:
        pid = int(pid)
        if pid in by_pid and pid not in seen:
            xi.append(copy.deepcopy(by_pid[pid]))
            seen.add(pid)
        if len(xi) == 11:
            break
    for p in squad:
        if len(xi) >= 11:
            break
        if int(p["pid"]) not in seen:
            xi.append(copy.deepcopy(p))
            seen.add(int(p["pid"]))
    if len(xi) < 11:
        raise GameError("squad_too_small", "You need at least 11 players in your squad to play.")
    bench = [copy.deepcopy(p) for p in squad if int(p["pid"]) not in seen]
    for i, p in enumerate(xi):
        p["slot"] = i
    for i, p in enumerate(bench):
        p["slot"] = 100 + i
    ai_name, ai_code = AI_TEAMS[cfg["difficulty"]]
    ai_xi = build_ai_squad(xi, cfg["difficulty"], ai_pool, rnd)
    user_name = user_team_name(first_name, franchise)
    now = time.time()
    return {
        "v": SCHEMA_VERSION,
        "id": "g" + uuid.uuid4().hex[:14],
        "uid": int(user_id),
        "rev": 1,
        "created": now,
        "updated": now,
        "stage": "xi",
        "cfg": dict(cfg),
        "teams": {
            "user": {"name": user_name, "code": team_code(user_name), "xi": xi, "bench": bench},
            "ai": {"name": ai_name, "code": ai_code, "xi": ai_xi, "bench": []},
        },
        "toss": None,
        "inn": [],
        "n": -1,
        "target": None,
        "result": None,
        "feed": [],
        "hist": {"user_bat": [], "user_bowl": []},
        "wp": {"user": 50.0, "series": [], "start": None},
        "xi_changes": 0,
        "balls_played": 0,
    }


def _require_stage(g: dict, *stages: str) -> None:
    if g["stage"] not in stages:
        raise GameError("wrong_stage", f"That action is not available right now ({g['stage']}).")


def xi_swap(g: dict, out_pid: int, in_pid: int) -> None:
    """Replace a Playing XI player with a substitute (takes the same batting
    position).  Matches the bot's /changeXL behaviour but only for this game."""
    _require_stage(g, "xi")
    team = g["teams"]["user"]
    xi, bench = team["xi"], team["bench"]
    oi = next((i for i, p in enumerate(xi) if int(p["pid"]) == int(out_pid)), None)
    bi = next((i for i, p in enumerate(bench) if int(p["pid"]) == int(in_pid)), None)
    if oi is None:
        raise GameError("player_not_in_xi", "That player is not in your Playing XI.")
    if bi is None:
        raise GameError("player_not_on_bench", "That player is not a substitute.")
    xi[oi], bench[bi] = bench[bi], xi[oi]
    g["xi_changes"] = int(g.get("xi_changes") or 0) + 1
    _resequence(team)


def xi_move(g: dict, pid: int, direction: int) -> None:
    """Move a Playing XI player up (-1) or down (+1) the batting order."""
    _require_stage(g, "xi")
    xi = g["teams"]["user"]["xi"]
    i = next((k for k, p in enumerate(xi) if int(p["pid"]) == int(pid)), None)
    if i is None:
        raise GameError("player_not_in_xi", "That player is not in your Playing XI.")
    j = i + (-1 if int(direction) < 0 else 1)
    if j < 0 or j >= len(xi):
        raise GameError("cannot_move", "Cannot move further.")
    xi[i], xi[j] = xi[j], xi[i]
    _resequence(g["teams"]["user"])


def _resequence(team: dict) -> None:
    for i, p in enumerate(team["xi"]):
        p["slot"] = i
    for i, p in enumerate(team["bench"]):
        p["slot"] = 100 + i


def xi_summary(g: dict) -> dict:
    """Quick quality read-out shown on the Playing XI screen."""
    xi = g["teams"]["user"]["xi"]
    bowlers = [p for p in xi if can_bowl(p)]
    keepers = [p for p in xi if p["role"] == "Wicketkeeper"]
    need = 2 if int(g["cfg"]["overs"]) <= 2 else 5
    warns = []
    if len(bowlers) < need:
        warns.append(f"Only {len(bowlers)} bowlers - part-timers will have to bowl")
    if not keepers:
        warns.append("No wicketkeeper in the XI - stumpings are off the table")
    bat_avg = sum(p["bat"] for p in xi) / 11.0
    top = sorted((p["bowl"] for p in bowlers), reverse=True)[:5]
    bowl_avg = sum(top) / len(top) if top else 0.0
    return {"bat_avg": round(bat_avg, 1), "bowl_avg": round(bowl_avg, 1),
            "bowlers": len(bowlers), "keepers": len(keepers), "warnings": warns,
            "overall": round((bat_avg + bowl_avg) / 2.0, 1)}


def xi_confirm(g: dict) -> None:
    _require_stage(g, "xi")
    xi = g["teams"]["user"]["xi"]
    if len(xi) != 11 or len({int(p["pid"]) for p in xi}) != 11:
        raise GameError("bad_xi", "Your Playing XI must have 11 different players.")
    _resequence(g["teams"]["user"])
    g["stage"] = "toss"
    _feed(g, {"k": "info", "text": "Playing XI confirmed. Time for the toss!"})


# ---------------------------------------------------------------------------
# toss
# ---------------------------------------------------------------------------
def toss_call(g: dict, call: str, rng: random.Random | None = None) -> dict:
    _require_stage(g, "toss")
    rnd = rng or random.Random()
    call = str(call or "").strip().lower()
    if call not in {"heads", "tails"}:
        raise GameError("bad_call", "Call heads or tails.")
    coin = rnd.choice(["heads", "tails"])
    winner = "user" if call == coin else "ai"
    toss = {"call": call, "coin": coin, "winner": winner, "decision": None, "decided_by": None}
    g["toss"] = toss
    if winner == "ai":
        decision = ai.pick_toss_decision(g["cfg"]["difficulty"], g["cfg"]["pitch"], rnd)
        toss["decision"] = decision
        toss["decided_by"] = "ai"
        _feed(g, {"k": "info", "text": f"{g['teams']['ai']['name']} won the toss and chose to "
                                       f"{'bat' if decision == 'bat' else 'bowl'} first."})
        _begin_match(g, rnd)
    else:
        g["stage"] = "decision"
        _feed(g, {"k": "info", "text": "You won the toss! Choose to bat or bowl first."})
    return toss


def toss_decide(g: dict, decision: str, rng: random.Random | None = None) -> None:
    _require_stage(g, "decision")
    decision = str(decision or "").strip().lower()
    if decision not in {"bat", "bowl"}:
        raise GameError("bad_decision", "Choose bat or bowl.")
    g["toss"]["decision"] = decision
    g["toss"]["decided_by"] = "user"
    _feed(g, {"k": "info", "text": f"You chose to {decision} first."})
    _begin_match(g, rng or random.Random())


def _begin_match(g: dict, rng: random.Random) -> None:
    toss = g["toss"]
    first_bat = toss["winner"] if toss["decision"] == "bat" else ("ai" if toss["winner"] == "user" else "user")
    g["first_bat"] = first_bat
    g["stage"] = "play"
    _start_innings(g, 0, first_bat, rng)


# ---------------------------------------------------------------------------
# innings set-up
# ---------------------------------------------------------------------------
def _new_batter(p: dict, entered_at: dict | None = None) -> dict:
    return {"slot": p["slot"], "name": p["name"], "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
            "dots": 0, "conf": 0.0, "streak": 0, "out": False, "how": None, "kind": None,
            "bowler": None, "state": "yet", "in_at": entered_at}


def _start_innings(g: dict, idx: int, batting: str, rng: random.Random) -> None:
    bowling = "ai" if batting == "user" else "user"
    xi = g["teams"][batting]["xi"]
    batters = [_new_batter(p) for p in xi]
    batters[0]["state"] = "in"
    batters[1]["state"] = "in"
    batters[0]["in_at"] = {"runs": 0, "ov": "0.0"}
    batters[1]["in_at"] = {"runs": 0, "ov": "0.0"}
    inn = {
        "idx": idx, "bat": batting, "bowl": bowling,
        "runs": 0, "wkts": 0, "balls": 0,
        "extras": {"wd": 0, "nb": 0, "b": 0, "lb": 0},
        "batters": batters, "striker": 0, "non": 1, "next": 2,
        "bowlers": {}, "bowl_order": [], "cur_bowler": None, "last_bowler": None,
        "over": {"no": 1, "balls": [], "runs": 0, "wkts": 0, "legal": 0, "bowler": None,
                 "bowler_runs": 0, "had_extra": False},
        "overs_log": [], "fow": [], "partner": {"runs": 0, "balls": 0, "a": 0, "b": 1},
        "partnerships": [], "free_hit": False, "run_outs": 0,
        "target": g.get("target") if idx == 1 else None,
        "done": False, "end_reason": None, "log": [],
        "hat": [None, 0], "pp_runs": None, "milestones": [],
        "fours": 0, "sixes": 0, "dots": 0, "peak_wp": None,
    }
    g["inn"].append(inn)
    g["n"] = idx
    _feed(g, {"k": "info", "text": (
        f"Innings {idx + 1}: {g['teams'][batting]['name']} to bat"
        + (f", target {inn['target']}." if inn["target"] else "."))})
    _begin_over(g, inn, rng)


def cur(g: dict) -> dict | None:
    n = int(g.get("n", -1))
    return g["inn"][n] if 0 <= n < len(g["inn"]) else None


def max_balls(g: dict) -> int:
    return int(g["cfg"]["overs"]) * 6


# ---------------------------------------------------------------------------
# bowler management
# ---------------------------------------------------------------------------
def _bowler_stat(inn: dict, slot: int) -> dict:
    key = str(slot)
    if key not in inn["bowlers"]:
        inn["bowlers"][key] = {"slot": slot, "balls": 0, "runs": 0, "wkts": 0, "maidens": 0, "dots": 0,
                               "wd": 0, "nb": 0, "fours": 0, "sixes": 0, "overs_done": 0, "best_over": None}
        inn["bowl_order"].append(slot)
    return inn["bowlers"][key]


def _overs_completed_by(inn: dict, slot: int) -> int:
    return int(inn["bowlers"].get(str(slot), {}).get("overs_done", 0))


def _part_timer_view(p: dict) -> dict:
    """A non-bowler thrown the ball: medium pace, level at least 25."""
    q = dict(p)
    q["bw"] = p.get("bw") or "RAM"
    q["bowl"] = max(25, int(p.get("bowl") or 0))
    q["part"] = not can_bowl(p)
    return q


def eligible_bowlers(g: dict, inn: dict | None = None) -> list[dict]:
    """Who may bowl the next over for the fielding side, with the bot's rules:
    quota of a fifth of the innings, no two overs in a row unless forced, and
    part-timers only when the real bowlers cannot cover the remaining overs."""
    inn = inn or cur(g)
    xi = g["teams"][inn["bowl"]]["xi"]
    quota = eng.bowler_over_quota(g["cfg"]["overs"])
    overs_left = int(g["cfg"]["overs"]) - len(inn["overs_log"])
    under = [p for p in xi if _overs_completed_by(inn, p["slot"]) < quota]
    capable = [p for p in under if can_bowl(p)]
    room = sum(quota - _overs_completed_by(inn, p["slot"]) for p in capable)
    last = inn["last_bowler"]
    cand = [p for p in capable if p["slot"] != last]
    if room < overs_left or not cand:
        cand += [p for p in under if not can_bowl(p) and p["slot"] != last]
    if not cand:
        cand = list(under)            # only the previous bowler is left
    if not cand:
        cand = list(xi)               # everybody is at the quota: anybody may bowl
    return [_part_timer_view(p) for p in cand]


def bowler_option(g: dict, inn: dict, p: dict) -> dict:
    stat = inn["bowlers"].get(str(p["slot"]), {})
    quota = eng.bowler_over_quota(g["cfg"]["overs"])
    balls = int(stat.get("balls", 0))
    return {
        "slot": p["slot"], "name": p["name"], "role": p["role"], "bw": p.get("bw"), "bowl": p["bowl"],
        "part": bool(p.get("part")), "overs": f"{balls // 6}.{balls % 6}",
        "runs": int(stat.get("runs", 0)), "wkts": int(stat.get("wkts", 0)),
        "left": max(0, quota - int(stat.get("overs_done", 0))),
        "econ": round(int(stat.get("runs", 0)) * 6.0 / balls, 2) if balls else None,
        "tactics": list(ai.TACTIC_KEYS[ai.tactic_set_key(p.get("bw"))]),
    }


def _begin_over(g: dict, inn: dict, rng: random.Random) -> None:
    """Start the next over: the computer picks its own bowler immediately, the
    user is asked (pending 'bowler') when the user is bowling."""
    inn["over"] = {"no": len(inn["overs_log"]) + 1, "balls": [], "runs": 0, "wkts": 0, "legal": 0,
                   "bowler": None, "bowler_runs": 0, "had_extra": False}
    inn["cur_bowler"] = None
    if inn["bowl"] == "ai":
        options = eligible_bowlers(g, inn)
        diff = g["cfg"]["difficulty"]
        cands = []
        for p in options:
            st = inn["bowlers"].get(str(p["slot"]), {})
            cands.append({"pid": p["slot"], "bowl": p["bowl"], "bw": p.get("bw"), "role": p["role"],
                          "overs_left": max(0, eng.bowler_over_quota(g["cfg"]["overs"]) - int(st.get("overs_done", 0))),
                          "wkts": int(st.get("wkts", 0)), "runs": int(st.get("runs", 0)),
                          "balls": int(st.get("balls", 0))})
        ctx = {"pitch": g["cfg"]["pitch"], "overs": g["cfg"]["overs"], "over_no": inn["over"]["no"],
               "balls_left": max_balls(g) - inn["balls"], "wkts_lost": inn["wkts"]}
        slot = ai.pick_bowler(diff, cands, ctx, rng)
        _assign_bowler(g, inn, slot)


def _assign_bowler(g: dict, inn: dict, slot: int) -> None:
    inn["cur_bowler"] = int(slot)
    inn["over"]["bowler"] = int(slot)
    _bowler_stat(inn, int(slot))


def choose_bowler(g: dict, slot: int) -> None:
    _require_stage(g, "play")
    inn = cur(g)
    if inn is None or inn["done"] or inn["bowl"] != "user":
        raise GameError("not_your_over", "You are not bowling right now.")
    if inn["cur_bowler"] is not None:
        raise GameError("bowler_chosen", "A bowler has already been chosen for this over.")
    allowed = {p["slot"] for p in eligible_bowlers(g, inn)}
    if int(slot) not in allowed:
        raise GameError("bowler_not_allowed", "That bowler cannot bowl this over.")
    _assign_bowler(g, inn, int(slot))


# ---------------------------------------------------------------------------
# pending action
# ---------------------------------------------------------------------------
def pending(g: dict) -> dict:
    stage = g["stage"]
    if stage in {"xi", "toss", "decision", "break", "done"}:
        return {"kind": {"xi": "xi", "toss": "toss_call", "decision": "toss_decision",
                         "break": "continue", "done": "done"}[stage]}
    inn = cur(g)
    if inn is None or inn["done"]:
        return {"kind": "continue"}
    if inn["bat"] == "user":
        return {"kind": "mindset", "options": list(eng.BATTER_MINDSETS)}
    if inn["cur_bowler"] is None:
        return {"kind": "bowler", "options": [bowler_option(g, inn, p) for p in eligible_bowlers(g, inn)]}
    p = g["teams"]["user"]["xi"][inn["cur_bowler"]]
    return {"kind": "tactic", "bowler": inn["cur_bowler"],
            "options": list(ai.TACTIC_KEYS[ai.tactic_set_key(p.get("bw") or "RAM")])}


# ---------------------------------------------------------------------------
# delivering
# ---------------------------------------------------------------------------
def _norm_choice(choice: str) -> str:
    c = str(choice or "").strip().lower().replace(" ", "_").replace("-", "_")
    return CHOICE_ALIASES.get(c, c)


def _ai_context(g: dict, inn: dict) -> dict:
    striker_slot = inn["batters"][inn["striker"]]["slot"]
    bat_p = g["teams"][inn["bat"]]["xi"][striker_slot]
    bowl_p = _part_timer_view(g["teams"][inn["bowl"]]["xi"][inn["cur_bowler"]])
    st = inn["batters"][inn["striker"]]
    return {
        "pitch": g["cfg"]["pitch"], "overs": g["cfg"]["overs"], "over_no": inn["over"]["no"],
        "difficulty": g["cfg"]["difficulty"], "batter_side": inn["bat"],
        "batter_level": bat_p["bat"], "bowler_level": bowl_p["bowl"],
        "bowler_style": bowl_p.get("bw"), "bowler_role": bowl_p["role"], "batter_role": bat_p["role"],
        "confidence": st["conf"], "balls_faced": st["balls"], "wkts_over": inn["over"]["wkts"],
        "target": inn["target"], "runs": inn["runs"], "balls_left": max_balls(g) - inn["balls"],
        "wkts": inn["wkts"], "position": inn["striker"] + 1, "over_runs": inn["over"]["runs"],
        "free_hit": inn["free_hit"],
        "bat_upg": bat_p.get("ub") if inn["bat"] == "user" else None,
        "bowl_upg": bowl_p.get("uw") if inn["bowl"] == "user" else None,
    }


def _resolve_outcome(g: dict, inn: dict, mindset: str, tactic: str, rng: random.Random):
    ctx = _ai_context(g, inn)
    weights = eng.resolve_weights(
        tactic, mindset, ctx["pitch"], ctx["over_no"], ctx["batter_level"], ctx["bowler_level"],
        ctx["confidence"], batsman_balls_faced=ctx["balls_faced"], wickets_this_over=ctx["wkts_over"],
        bowler_style=ctx["bowler_style"], bowler_role=ctx["bowler_role"], batter_role=ctx["batter_role"],
        target=ctx["target"], total_runs=ctx["runs"], balls_remaining=ctx["balls_left"],
        wickets_in_hand=10 - ctx["wkts"], batting_position=ctx["position"], over_runs=ctx["over_runs"],
        free_hit_next_ball=ctx["free_hit"], batting_upgrade_context=ctx["bat_upg"],
        bowling_upgrade_context=ctx["bowl_upg"], max_overs=ctx["overs"], difficulty=ctx["difficulty"],
        batter_side=ctx["batter_side"],
    )
    values = [weights.get(k, 0.0) for k in eng.OUTCOMES]
    outcome = rng.choices(eng.OUTCOMES, weights=values, k=1)[0]
    if outcome == "W" and ctx["free_hit"]:
        outcome = 0
    return outcome


def _conf_delta(name: str, conf: float, streak_before: int) -> float:
    if name == "dot":
        return -3.0 if conf < 25.0 else (-2.0 if conf < 40.0 else -1.0)
    if name == "single":
        return 1.0
    if name == "double":
        return 2.0
    if name == "triple":
        return 3.0
    if name == "four":
        return 5.0 + (2.0 if streak_before >= 1 else 0.0)
    if name == "six":
        return 6.0 + (2.0 if streak_before >= 1 else 0.0)
    return 0.0


def _ov_label(legal_balls_after: int, legal: bool) -> str:
    if legal:
        n = legal_balls_after - 1
        return f"{n // 6}.{n % 6 + 1}"
    return f"{legal_balls_after // 6}.{legal_balls_after % 6 + 1}"


def _fielders(g: dict, side: str) -> list[dict]:
    return [{"name": p["name"], "role": p["role"], "player_id": p["slot"]} for p in g["teams"][side]["xi"]]


def deliver_one(g: dict, mindset: str, tactic: str, rng: random.Random) -> dict:
    """Resolve exactly one delivery with the given (already validated) choices
    and return its event dict.  Mutates the game."""
    inn = cur(g)
    bat_side, bowl_side = inn["bat"], inn["bowl"]
    striker = inn["batters"][inn["striker"]]
    bowl_slot = inn["cur_bowler"]
    bowl_p = _part_timer_view(g["teams"][bowl_side]["xi"][bowl_slot])
    bstat = _bowler_stat(inn, bowl_slot)
    free_hit = bool(inn["free_hit"])
    outcome = _resolve_outcome(g, inn, mindset, tactic, rng)
    name = _NAME[outcome]
    legal = outcome not in ("WD", "NB")
    wicket = outcome == "W"
    runs = 0 if wicket else (outcome if isinstance(outcome, int) else (1 if outcome in ("WD", "NB", "BY", "LB") else 0))
    bat_runs = runs if isinstance(outcome, int) and outcome in (1, 2, 3, 4, 6) else 0
    bowler_runs = runs if outcome not in ("BY", "LB") else 0

    dismissal = None
    if wicket:
        keeper_ok = any(p["role"] == "Wicketkeeper" and p["slot"] != bowl_slot for p in g["teams"][bowl_side]["xi"])
        kind = eng.pick_wicket_kind(bowl_p.get("bw"), bowl_p["role"], inn["run_outs"], keeper_ok, rng)
        bowler_dict = {"name": bowl_p["name"], "role": bowl_p["role"], "player_id": bowl_slot}
        dismissal = eng.build_dismissal(kind, bowler_dict, _fielders(g, bowl_side), rng)
        if dismissal["kind"] == "run_out":
            inn["run_outs"] += 1

    # confidence / streak (before the batter's own tally changes)
    streak_before = int(striker["streak"])
    cname = _CONF_NAME.get(outcome) if not wicket else None
    if cname:
        striker["conf"] = max(0.0, min(100.0, striker["conf"] + _conf_delta(cname, striker["conf"], streak_before)))
        if cname in ("four", "six"):
            striker["streak"] = streak_before + 1
        else:
            striker["streak"] = 0

    # score
    inn["runs"] += runs
    if outcome == "WD":
        inn["extras"]["wd"] += 1
    elif outcome == "NB":
        inn["extras"]["nb"] += 1
    elif outcome == "BY":
        inn["extras"]["b"] += 1
    elif outcome == "LB":
        inn["extras"]["lb"] += 1
    if legal:
        inn["balls"] += 1
        inn["over"]["legal"] += 1
        g["balls_played"] = int(g.get("balls_played") or 0) + 1
        bstat["balls"] += 1
        if striker is not None and outcome not in ("WD", "NB"):
            striker["balls"] += 1
    inn["over"]["runs"] += runs
    if outcome in ("WD", "NB"):
        inn["over"]["had_extra"] = True
    bstat["runs"] += bowler_runs
    inn["over"]["bowler_runs"] += bowler_runs
    if outcome == "WD":
        bstat["wd"] += 1
    if outcome == "NB":
        bstat["nb"] += 1
    if legal and runs == 0:
        bstat["dots"] += 1
        inn["dots"] += 1
    if outcome == 0 and legal:
        striker["dots"] += 1
    if bat_runs:
        striker["runs"] += bat_runs
        if bat_runs == 4:
            striker["fours"] += 1
            bstat["fours"] += 1
            inn["fours"] += 1
        elif bat_runs == 6:
            striker["sixes"] += 1
            bstat["sixes"] += 1
            inn["sixes"] += 1
    inn["partner"]["runs"] += runs
    if legal:
        inn["partner"]["balls"] += 1

    # per-approach tally for the user's own choices (match centre insight)
    cs = g.setdefault("cs", {"bat": {}, "bowl": {}})
    if bat_side == "user":
        row = cs["bat"].setdefault(mindset, [0, 0, 0, 0])
    else:
        row = cs["bowl"].setdefault(tactic, [0, 0, 0, 0])
    row[0] += 1 if legal else 0
    row[1] += runs
    row[2] += 1 if wicket else 0
    row[3] += 1 if outcome in (4, 6) else 0

    token = _SYMBOL[outcome]
    inn["over"]["balls"].append(token)
    label = _ov_label(inn["balls"], legal)

    milestones: list[str] = []
    if wicket:
        inn["wkts"] += 1
        over_wkt_ball = _ov_label(inn["balls"], True)
        inn["over"]["wkts"] += 1
        striker["out"] = True
        striker["state"] = "out"
        striker["how"] = dismissal["text"]
        striker["kind"] = dismissal["kind"]
        striker["bowler"] = bowl_slot if dismissal["bowler_credit"] else None
        if dismissal["bowler_credit"]:
            bstat["wkts"] += 1
        inn["fow"].append({"w": inn["wkts"], "runs": inn["runs"], "slot": striker["slot"],
                           "ov": over_wkt_ball, "partner_runs": inn["partner"]["runs"]})
        inn["partnerships"].append({"runs": inn["partner"]["runs"], "balls": inn["partner"]["balls"],
                                    "a": inn["partner"]["a"], "b": inn["partner"]["b"], "wicket": inn["wkts"]})
        # hat-trick tracking (legal balls only)
        if dismissal["bowler_credit"]:
            if inn["hat"][0] == bowl_slot and inn["hat"][1] >= 1:
                inn["hat"][1] += 1
            else:
                inn["hat"] = [bowl_slot, 1]
            if inn["hat"][1] == 3:
                milestones.append(com.milestone_line("hat_trick", bowl_p["name"]))
        else:
            inn["hat"] = [None, 0]
        if bstat["wkts"] == 3 and dismissal["bowler_credit"]:
            milestones.append(com.milestone_line("three_wickets", bowl_p["name"]))
        if bstat["wkts"] == 5 and dismissal["bowler_credit"]:
            milestones.append(com.milestone_line("five_wickets", bowl_p["name"]))
    elif legal:
        inn["hat"] = [None, 0]

    # batter milestones
    if bat_runs and striker["runs"] - bat_runs < 50 <= striker["runs"]:
        milestones.append(com.milestone_line("fifty", striker["name"], f"{striker['balls']} balls"))
        inn["milestones"].append({"k": "fifty", "slot": striker["slot"]})
    if bat_runs and striker["runs"] - bat_runs < 100 <= striker["runs"]:
        milestones.append(com.milestone_line("hundred", striker["name"]))
        inn["milestones"].append({"k": "hundred", "slot": striker["slot"]})

    # strike rotation (not wide / no-ball), then ends change at the over's end
    if outcome not in ("WD", "NB") and runs % 2 == 1:
        inn["striker"], inn["non"] = inn["non"], inn["striker"]
    over_ended = legal and inn["balls"] % 6 == 0
    if over_ended:
        inn["striker"], inn["non"] = inn["non"], inn["striker"]
    if wicket and inn["wkts"] < 10:
        new_idx = inn["next"]
        inn["next"] = new_idx + 1
        newb = inn["batters"][new_idx]
        newb["state"] = "in"
        newb["in_at"] = {"runs": inn["runs"], "ov": _ov_label(inn["balls"], True)}
        if over_ended:
            inn["non"] = new_idx
        else:
            inn["striker"] = new_idx
        inn["partner"] = {"runs": 0, "balls": 0, "a": inn["striker"], "b": inn["non"]}

    inn["free_hit"] = outcome == "NB"

    # powerplay marker (first 6 overs of a T20-like game, scaled for short games)
    pp_balls = _powerplay_balls(g)
    if inn["pp_runs"] is None and legal and inn["balls"] >= pp_balls:
        inn["pp_runs"] = inn["runs"]

    text = com.ball_line(outcome, bat=striker["name"], bowl=bowl_p["name"], runs=runs,
                         wicket_kind=(dismissal or {}).get("kind"), free_hit=free_hit, rng=rng)
    event = {
        "k": "ball", "inn": inn["idx"], "ov": label, "over_no": inn["over"]["no"],
        "bat": striker["name"], "bat_slot": striker["slot"], "bowl": bowl_p["name"], "bowl_slot": bowl_slot,
        "sym": token, "runs": runs, "kind": name, "wicket": wicket, "legal": legal,
        "dis": dismissal["text"] if dismissal else None, "dis_kind": dismissal["kind"] if dismissal else None,
        "bat_choice": mindset, "bowl_choice": tactic, "free_hit": free_hit, "text": text,
        "score": f"{inn['runs']}/{inn['wkts']}", "ms": milestones,
        "bat_side": bat_side,
    }
    inn["log"].append([label, token, runs, striker["slot"], bowl_slot, 1 if wicket else 0, inn["over"]["no"]])

    # end of over bookkeeping
    if over_ended:
        _close_over(g, inn)
    _check_innings_end(g, inn)
    if inn["done"] and not over_ended and inn["over"]["balls"]:
        _close_over(g, inn, partial=True)
    return event


def _powerplay_balls(g: dict) -> int:
    n = int(g["cfg"]["overs"])
    return {20: 36, 10: 18, 5: 12, 2: 6}.get(n, max(6, n * 6 * 3 // 10))


def _close_over(g: dict, inn: dict, partial: bool = False) -> None:
    ov = inn["over"]
    slot = ov["bowler"]
    if slot is None:
        return
    bstat = _bowler_stat(inn, slot)
    maiden = (not partial) and ov["legal"] == 6 and ov["bowler_runs"] == 0 and not ov["had_extra"]
    if maiden:
        bstat["maidens"] += 1
    if not partial:
        bstat["overs_done"] += 1
        if bstat["best_over"] is None or ov["bowler_runs"] < bstat["best_over"]:
            bstat["best_over"] = ov["bowler_runs"]
    entry = {"no": ov["no"], "bowler": slot, "runs": ov["runs"], "wkts": ov["wkts"],
             "balls": list(ov["balls"]), "maiden": bool(maiden), "cum": inn["runs"], "cw": inn["wkts"],
             "partial": bool(partial)}
    inn["overs_log"].append(entry)
    if not partial:
        inn["last_bowler"] = slot
    if ov["balls"]:
        _feed(g, {"k": "over", "inn": inn["idx"], "text": com.over_summary(
            ov["no"], ov["runs"], ov["wkts"], g["teams"][inn["bowl"]]["xi"][slot]["name"], maiden),
            "score": f"{inn['runs']}/{inn['wkts']}"})


def _check_innings_end(g: dict, inn: dict) -> None:
    if inn["done"]:
        return
    reason = None
    if inn["wkts"] >= 10:
        reason = "all_out"
    elif inn["target"] is not None and inn["runs"] >= inn["target"]:
        reason = "target"
    elif inn["balls"] >= max_balls(g):
        reason = "overs"
    if reason:
        inn["done"] = True
        inn["end_reason"] = reason
        inn["partnerships"].append({"runs": inn["partner"]["runs"], "balls": inn["partner"]["balls"],
                                    "a": inn["partner"]["a"], "b": inn["partner"]["b"], "wicket": None})
        for b in inn["batters"]:
            if b["state"] == "in":
                b["state"] = "notout"


def _finish_innings_if_done(g: dict, rng: random.Random) -> None:
    inn = cur(g)
    if inn is None or not inn["done"]:
        return
    if inn["idx"] == 0:
        g["target"] = inn["runs"] + 1
        g["stage"] = "break"
        n_bat = g["teams"][inn["bat"]]["name"]
        _feed(g, {"k": "info", "text": f"Innings break: {n_bat} {inn['runs']}/{inn['wkts']}. "
                                        f"Target {g['target']}."})
    else:
        _finish_match(g)


def continue_innings(g: dict, rng: random.Random | None = None) -> None:
    """Innings break -> start the chase."""
    _require_stage(g, "break")
    rnd = rng or random.Random()
    first = g["inn"][0]
    g["stage"] = "play"
    _start_innings(g, 1, first["bowl"], rnd)


# ---------------------------------------------------------------------------
# user actions
# ---------------------------------------------------------------------------
def _validate_choice(g: dict, inn: dict, choice: str) -> str:
    choice = _norm_choice(choice)
    if inn["bat"] == "user":
        if choice not in eng.BATTER_MINDSETS:
            raise GameError("bad_choice", "Pick a batting approach.")
    else:
        if inn["cur_bowler"] is None:
            raise GameError("need_bowler", "Choose your bowler first.")
        p = g["teams"]["user"]["xi"][inn["cur_bowler"]]
        allowed = ai.TACTIC_KEYS[ai.tactic_set_key(p.get("bw") or "RAM")]
        if choice not in allowed:
            raise GameError("bad_choice", "That delivery is not available for this bowler.")
    return choice


def _ai_pick(g: dict, inn: dict, rng: random.Random) -> str:
    ctx = _ai_context(g, inn)
    if inn["bat"] == "ai":
        return ai.pick_mindset(ctx, g["hist"]["user_bowl"], rng)
    return ai.pick_tactic(ctx, g["hist"]["user_bat"], rng)


def _push_hist(g: dict, side_key: str, value: str) -> None:
    arr = g["hist"][side_key]
    arr.append(value)
    if len(arr) > MAX_HIST:
        del arr[:len(arr) - MAX_HIST]


def play_ball(g: dict, choice: str, rng: random.Random | None = None) -> list[dict]:
    """Ball by ball: the user's choice decides this single delivery."""
    _require_stage(g, "play")
    rnd = rng or random.Random()
    inn = cur(g)
    if inn is None or inn["done"]:
        raise GameError("innings_over", "This innings is over.")
    choice = _validate_choice(g, inn, choice)
    ai_choice = _ai_pick(g, inn, rnd)
    if inn["bat"] == "user":
        mindset, tactic = choice, ai_choice
        _push_hist(g, "user_bat", mindset)
    else:
        mindset, tactic = ai_choice, choice
        _push_hist(g, "user_bowl", tactic)
    events = [deliver_one(g, mindset, tactic, rnd)]
    _after_delivery(g, inn, rnd)
    for ev in events:
        _feed(g, {"k": "ball", **{k: ev[k] for k in ("inn", "ov", "sym", "text", "score", "wicket", "bat", "bowl")}})
        for m in ev["ms"]:
            _feed(g, {"k": "milestone", "text": m})
    return events


def play_over(g: dict, choice: str, rng: random.Random | None = None) -> list[dict]:
    """Over by over: one choice for the whole over, resolved immediately."""
    _require_stage(g, "play")
    rnd = rng or random.Random()
    inn = cur(g)
    if inn is None or inn["done"]:
        raise GameError("innings_over", "This innings is over.")
    choice = _validate_choice(g, inn, choice)
    ai_choice = _ai_pick(g, inn, rnd)
    events: list[dict] = []
    guard = 0
    while not inn["done"] and inn["over"]["legal"] < 6 and guard < 30:
        guard += 1
        if inn["bat"] == "user":
            mindset, tactic = choice, ai_choice
        else:
            mindset, tactic = ai_choice, choice
        events.append(deliver_one(g, mindset, tactic, rnd))
    if inn["bat"] == "user":
        _push_hist(g, "user_bat", choice)
    else:
        _push_hist(g, "user_bowl", choice)
    _after_delivery(g, inn, rnd)
    for ev in events:
        _feed(g, {"k": "ball", **{k: ev[k] for k in ("inn", "ov", "sym", "text", "score", "wicket", "bat", "bowl")}})
        for m in ev["ms"]:
            _feed(g, {"k": "milestone", "text": m})
    return events


def _after_delivery(g: dict, inn: dict, rng: random.Random) -> None:
    if inn["done"]:
        _finish_innings_if_done(g, rng)
        return
    if inn["over"]["legal"] >= 6:
        _begin_over(g, inn, rng)


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------
def _finish_match(g: dict) -> None:
    a, b = g["inn"][0], g["inn"][1]
    g["stage"] = "done"
    names = {s: g["teams"][s]["name"] for s in ("user", "ai")}
    if b["runs"] >= (b["target"] or 10 ** 9):
        winner, mtype, margin = b["bat"], "wickets", 10 - b["wkts"]
        text = f"{names[winner]} won by {margin} wicket{'s' if margin != 1 else ''}"
    elif b["runs"] == (b["target"] or 0) - 1:
        winner, mtype, margin = "tie", "tie", 0
        text = "Match tied"
    else:
        winner, mtype, margin = a["bat"], "runs", a["runs"] - b["runs"]
        text = f"{names[winner]} won by {margin} run{'s' if margin != 1 else ''}"
    g["result"] = {"winner": winner, "type": mtype, "margin": margin, "text": text,
                   "forfeit": False, "balls_left": max(0, max_balls(g) - b["balls"]) if mtype == "wickets" else 0}
    g["result"]["pom"] = player_of_match(g)
    _feed(g, {"k": "result", "text": text})


def forfeit(g: dict) -> None:
    if g["stage"] == "done":
        return
    started = bool(g["inn"])
    g["stage"] = "done"
    g["result"] = {"winner": "ai", "type": "forfeit", "margin": 0, "forfeit": True, "started": started,
                   "text": f"You left the match - {g['teams']['ai']['name']} win by forfeit", "pom": None}
    _feed(g, {"k": "result", "text": g["result"]["text"]})


def _bat_points(b: dict) -> float:
    if b["balls"] <= 0 and b["runs"] <= 0:
        return 0.0
    sr = b["runs"] * 100.0 / max(1, b["balls"])
    return b["runs"] * 1.0 + b["fours"] * 0.5 + b["sixes"] * 1.0 + max(0.0, (sr - 100.0)) * 0.05 * min(1.0, b["balls"] / 12.0)


def _bowl_points(s: dict) -> float:
    if s["balls"] <= 0:
        return 0.0
    econ = s["runs"] * 6.0 / s["balls"]
    return s["wkts"] * 22.0 + s["maidens"] * 5.0 + s["dots"] * 0.4 + max(0.0, 8.0 - econ) * 1.2 * (s["balls"] / 24.0)


def player_of_match(g: dict) -> dict | None:
    """Best all-round contribution (batting + bowling points), with a small
    bonus for the winning side."""
    totals: dict[tuple[str, int], float] = {}
    for inn in g["inn"]:
        for b in inn["batters"]:
            key = (inn["bat"], b["slot"])
            totals[key] = totals.get(key, 0.0) + _bat_points(b)
        for slot_key, st in inn["bowlers"].items():
            key = (inn["bowl"], int(slot_key))
            totals[key] = totals.get(key, 0.0) + _bowl_points(st)
    if not totals:
        return None
    winner = (g.get("result") or {}).get("winner")
    ranked = sorted(totals.items(), key=lambda kv: -(kv[1] + (6.0 if kv[0][0] == winner else 0.0)))
    (side, slot), pts = ranked[0]
    p = g["teams"][side]["xi"][slot]
    lines = []
    for inn in g["inn"]:
        if inn["bat"] == side:
            b = inn["batters"][slot]
            if b["balls"] or b["runs"]:
                lines.append(f"{b['runs']} ({b['balls']})")
        if inn["bowl"] == side and str(slot) in inn["bowlers"]:
            st = inn["bowlers"][str(slot)]
            if st["balls"]:
                lines.append(f"{st['wkts']}/{st['runs']}")
    return {"side": side, "slot": slot, "name": p["name"], "pid": p.get("pid"), "eid": p.get("eid"),
            "kind": p.get("kind"), "line": " & ".join(lines) or "-", "pts": round(pts, 1)}


# ---------------------------------------------------------------------------
# misc helpers
# ---------------------------------------------------------------------------
def _feed(g: dict, item: dict) -> None:
    item = dict(item)
    item["t"] = int(time.time())
    g["feed"].append(item)
    if len(g["feed"]) > MAX_FEED:
        del g["feed"][:len(g["feed"]) - MAX_FEED]


def touch(g: dict) -> None:
    g["rev"] = int(g.get("rev") or 0) + 1
    g["updated"] = time.time()


def over_complete_or_stage_changed(before_stage: str, before_over: int, g: dict) -> bool:
    """True when the state is worth persisting (stage change or over end)."""
    inn = cur(g)
    if g["stage"] != before_stage or g["stage"] in {"done", "break"}:
        return True
    if inn is None:
        return True
    return inn["over"]["no"] != before_over


def simple_summary(g: dict) -> dict:
    """Tiny card for the 'resume your game' banner."""
    inn = cur(g)
    out = {"id": g["id"], "stage": g["stage"], "difficulty": g["cfg"]["difficulty"],
           "overs": g["cfg"]["overs"], "sim": g["cfg"]["sim"], "pitch": g["cfg"]["pitch"],
           "user": g["teams"]["user"]["name"], "ai": g["teams"]["ai"]["name"], "updated": g["updated"]}
    if inn:
        out["score"] = f"{inn['runs']}/{inn['wkts']}"
        out["overs_text"] = f"{inn['balls'] // 6}.{inn['balls'] % 6}"
        out["innings"] = inn["idx"] + 1
        out["batting"] = inn["bat"]
        out["target"] = inn["target"]
    return out
