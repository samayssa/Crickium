"""JSON view of a Mini App game for the screens.

Everything the UI needs is derived here from the game dict, so the client
never has to understand the engine's internal structure.  Nothing in a view
leaks information the player should not have (the computer's next choice is
never decided before the player acts; it is only revealed after a ball).
"""

from __future__ import annotations

from typing import Any

import engines.miniapp_tactics_engine as eng
import services.miniapp_game_ai as ai
import services.miniapp_game_engine as ge
from services.miniapp_game_squads import can_bowl
from utils.rarity import get_rarity

BW_LABELS = {
    "RAF": "Right-arm fast", "LAF": "Left-arm fast", "RAM": "Right-arm medium", "LAM": "Left-arm medium",
    "RAO": "Right-arm off break", "LAO": "Left-arm off break", "RAL": "Right-arm leg spin",
    "LAL": "Left-arm leg spin",
}
ROLE_ICONS = {"Wicketkeeper": "🧤", "AllRounder": "⚡", "Bowler": "🎯", "Batsman": "🏏"}
ROLE_LABELS = {"Wicketkeeper": "Wicketkeeper", "AllRounder": "All-rounder", "Bowler": "Bowler", "Batsman": "Batter"}
STAGE_LABELS = {"xi": "Playing XI", "toss": "Toss", "decision": "Toss", "play": "Live", "break": "Innings break",
                "done": "Result"}
ZONE_LABELS = {"nervous": "Nervous", "building": "Building", "set": "Set", "in_the_zone": "In the zone"}
PITCH_TIPS = {
    "even": "Balanced pitch - mix your approaches by the situation.",
    "flat": "Flat deck. Batters can attack, bowlers need variation.",
    "hard": "True bounce. Strokes come easily, boundaries flow.",
    "green": "Seam movement. Rotate early, attack only when set.",
    "bouncy": "Extra bounce helps pace. Avoid reckless hitting early.",
    "dry": "Spin and grip. Spinners are dangerous, bat sensibly.",
    "dusty": "Turn and low bounce. Wickets fall to aggression.",
    "slow": "Hard to time. Rotate the strike and build.",
}


def ovtxt(balls: int) -> str:
    return f"{int(balls) // 6}.{int(balls) % 6}"


def _sr(runs: int, balls: int) -> float:
    return round(runs * 100.0 / balls, 1) if balls else 0.0


def _econ(runs: int, balls: int) -> float:
    return round(runs * 6.0 / balls, 2) if balls else 0.0


def _img(p: dict) -> str | None:
    eid = int(p.get("eid") or 0)
    if eid <= 0 and p.get("kind") == "global":
        return None
    return f"/api/image/player?kind={p.get('kind') or 'global'}&id={eid}"


def player_card(p: dict, pos: int | None = None) -> dict:
    overall = max(int(p["bat"]), int(p["bowl"]))
    return {
        "pid": p["pid"], "slot": p["slot"], "name": p["name"], "role": p["role"],
        "role_icon": ROLE_ICONS.get(p["role"], "👤"), "role_label": ROLE_LABELS.get(p["role"], p["role"]),
        "bat": p["bat"], "bowl": p["bowl"], "overall": overall, "rarity": get_rarity(overall),
        "bh": p.get("bh"), "bw": p.get("bw"), "bw_label": BW_LABELS.get(p.get("bw") or "", None),
        "can_bowl": can_bowl(p), "upg": bool(p.get("ub") or p.get("uw")), "img": _img(p), "pos": pos,
    }


# ---------------------------------------------------------------------------
# scorecards
# ---------------------------------------------------------------------------
def _name(g: dict, side: str, slot: int) -> str:
    return g["teams"][side]["xi"][int(slot)]["name"]


def _extras_text(inn: dict) -> tuple[int, str]:
    e = inn["extras"]
    total = e["wd"] + e["nb"] + e["b"] + e["lb"]
    parts = []
    for key, label in (("b", "b"), ("lb", "lb"), ("wd", "w"), ("nb", "nb")):
        if e[key]:
            parts.append(f"{label} {e[key]}")
    return total, ", ".join(parts)


def batter_rows(g: dict, inn: dict) -> list[dict]:
    rows = []
    xi = g["teams"][inn["bat"]]["xi"]
    for idx, b in enumerate(inn["batters"]):
        p = xi[b["slot"]]
        bowler = _name(g, inn["bowl"], b["bowler"]) if b.get("bowler") is not None else None
        rows.append({
            "pos": idx + 1, "slot": b["slot"], "name": b["name"], "role_icon": ROLE_ICONS.get(p["role"], "👤"),
            "state": b["state"], "runs": b["runs"], "balls": b["balls"], "fours": b["fours"], "sixes": b["sixes"],
            "dots": b["dots"], "sr": _sr(b["runs"], b["balls"]), "how": b["how"], "kind": b["kind"],
            "bowler": bowler, "conf": int(round(b["conf"])), "bat": p["bat"],
            "striker": (idx == inn["striker"] and not inn["done"]),
            "in_at": b.get("in_at"),
        })
    return rows


def bowler_rows(g: dict, inn: dict) -> list[dict]:
    rows = []
    for slot in inn["bowl_order"]:
        st = inn["bowlers"][str(slot)]
        p = g["teams"][inn["bowl"]]["xi"][slot]
        balls = st["balls"]
        rows.append({
            "slot": slot, "name": p["name"], "bw": p.get("bw"), "bw_label": BW_LABELS.get(p.get("bw") or ""),
            "overs": ovtxt(balls), "balls": balls, "maidens": st["maidens"], "runs": st["runs"],
            "wkts": st["wkts"], "econ": _econ(st["runs"], balls), "dots": st["dots"], "wd": st["wd"],
            "nb": st["nb"], "fours": st["fours"], "sixes": st["sixes"],
            "current": (slot == inn["cur_bowler"] and not inn["done"]),
            "dot_pct": round(100.0 * st["dots"] / balls) if balls else 0,
        })
    return rows


def fow_rows(g: dict, inn: dict) -> list[dict]:
    return [{"w": f["w"], "runs": f["runs"], "name": _name(g, inn["bat"], f["slot"]), "ov": f["ov"],
             "partner_runs": f.get("partner_runs", 0)} for f in inn["fow"]]


def partnership_rows(g: dict, inn: dict) -> list[dict]:
    rows = []
    for i, pt in enumerate(inn["partnerships"]):
        a = inn["batters"][pt["a"]]["name"] if pt["a"] < len(inn["batters"]) else "?"
        b = inn["batters"][pt["b"]]["name"] if pt["b"] < len(inn["batters"]) else "?"
        rows.append({"no": i + 1, "runs": pt["runs"], "balls": pt["balls"], "a": a, "b": b,
                     "wicket": pt.get("wicket")})
    return rows


def over_rows(g: dict, inn: dict) -> list[dict]:
    rows = []
    for o in inn["overs_log"]:
        rows.append({"no": o["no"], "runs": o["runs"], "wkts": o["wkts"], "balls": o["balls"],
                     "maiden": o["maiden"], "cum": o["cum"], "cw": o["cw"],
                     "bowler": _name(g, inn["bowl"], o["bowler"]) if o["bowler"] is not None else "-",
                     "partial": o.get("partial", False)})
    return rows


def scorecard(g: dict, inn: dict) -> dict:
    extras_total, extras_text = _extras_text(inn)
    team = g["teams"][inn["bat"]]
    dnb = [b["name"] for b in inn["batters"] if b["state"] == "yet"]
    return {
        "idx": inn["idx"], "bat": inn["bat"], "bowl": inn["bowl"], "team": team["name"], "code": team["code"],
        "bowling_team": g["teams"][inn["bowl"]]["name"],
        "runs": inn["runs"], "wkts": inn["wkts"], "overs": ovtxt(inn["balls"]), "balls": inn["balls"],
        "rr": _econ(inn["runs"], inn["balls"]), "target": inn["target"], "done": inn["done"],
        "end_reason": inn["end_reason"], "extras": extras_total, "extras_text": extras_text,
        "batters": batter_rows(g, inn), "did_not_bat": dnb, "bowlers": bowler_rows(g, inn),
        "fow": fow_rows(g, inn), "partnerships": partnership_rows(g, inn), "overs_log": over_rows(g, inn),
        "pp_runs": inn.get("pp_runs"), "live": (g["n"] == inn["idx"] and g["stage"] == "play"),
    }


# ---------------------------------------------------------------------------
# live match block
# ---------------------------------------------------------------------------
def _phase_name(g: dict, over_no: int) -> str:
    return {"pp": "Powerplay", "mid": "Middle overs", "death": "Death overs"}[
        ai.phase_of(over_no, int(g["cfg"]["overs"]))]


def _batter_live(g: dict, inn: dict, idx: int) -> dict | None:
    if idx is None or idx < 0 or idx >= len(inn["batters"]):
        return None
    b = inn["batters"][idx]
    p = g["teams"][inn["bat"]]["xi"][b["slot"]]
    zone = eng.confidence_zone(b["conf"])
    return {"slot": b["slot"], "name": b["name"], "runs": b["runs"], "balls": b["balls"], "fours": b["fours"],
            "sixes": b["sixes"], "sr": _sr(b["runs"], b["balls"]), "conf": int(round(b["conf"])),
            "zone": zone, "zone_label": ZONE_LABELS[zone], "bat": p["bat"], "bh": p.get("bh"),
            "role_icon": ROLE_ICONS.get(p["role"], "👤"), "img": _img(p), "dots": b["dots"]}


def match_block(g: dict, inn: dict) -> dict:
    total_balls = ge.max_balls(g)
    balls_left = max(0, total_balls - inn["balls"])
    crr = _econ(inn["runs"], inn["balls"])
    par = round(ai.par_rpo(g["cfg"]["pitch"], int(g["cfg"]["overs"])), 2)
    target = inn["target"]
    need = rrr = None
    if target is not None:
        need = max(0, target - inn["runs"])
        rrr = round(need * 6.0 / balls_left, 2) if balls_left else None
    wp = g.get("wp") or {}
    proj = None
    if target is None and inn["balls"] >= 6:
        proj = int(round(inn["runs"] + crr * balls_left / 6.0))
    bowler = None
    if inn["cur_bowler"] is not None:
        p = ge._part_timer_view(g["teams"][inn["bowl"]]["xi"][inn["cur_bowler"]])
        st = inn["bowlers"].get(str(inn["cur_bowler"]), {})
        balls = st.get("balls", 0)
        bowler = {"slot": p["slot"], "name": p["name"], "bw": p.get("bw"), "bw_label": BW_LABELS.get(p.get("bw") or ""),
                  "bowl": p["bowl"], "role_icon": ROLE_ICONS.get(p["role"], "👤"), "img": _img(p),
                  "overs": ovtxt(balls), "maidens": st.get("maidens", 0), "runs": st.get("runs", 0),
                  "wkts": st.get("wkts", 0), "econ": _econ(st.get("runs", 0), balls),
                  "part_time": bool(p.get("part")), "family": eng.bowler_family(p.get("bw"), p.get("role"))}
    last_wkt = None
    if inn["fow"]:
        f = inn["fow"][-1]
        b = next((x for x in inn["batters"] if x["slot"] == f["slot"]), None)
        last_wkt = f"{b['name']} {b['runs']} ({b['balls']}) - {f['runs']}/{f['w']} at {f['ov']} ov" if b else None
    over_no = inn["over"]["no"]
    return {
        "no": inn["idx"] + 1, "bat": inn["bat"], "bowl": inn["bowl"],
        "bat_name": g["teams"][inn["bat"]]["name"], "bat_code": g["teams"][inn["bat"]]["code"],
        "bowl_name": g["teams"][inn["bowl"]]["name"], "bowl_code": g["teams"][inn["bowl"]]["code"],
        "runs": inn["runs"], "wkts": inn["wkts"], "balls": inn["balls"], "overs": ovtxt(inn["balls"]),
        "max_overs": int(g["cfg"]["overs"]), "balls_left": balls_left, "crr": crr, "par": par,
        "target": target, "need": need, "rrr": rrr, "proj": proj,
        "over_no": over_no, "this_over": list(inn["over"]["balls"]), "over_runs": inn["over"]["runs"],
        "free_hit": bool(inn["free_hit"]), "phase": _phase_name(g, over_no),
        "striker": _batter_live(g, inn, inn["striker"]), "non": _batter_live(g, inn, inn["non"]),
        "bowler": bowler, "partnership": {"runs": inn["partner"]["runs"], "balls": inn["partner"]["balls"]},
        "last_wicket": last_wkt, "done": inn["done"], "end_reason": inn["end_reason"],
        "pp_runs": inn.get("pp_runs"), "wkts_left": 10 - inn["wkts"],
        "last_over": ({"no": inn["overs_log"][-1]["no"], "runs": inn["overs_log"][-1]["runs"],
                       "wkts": inn["overs_log"][-1]["wkts"], "balls": inn["overs_log"][-1]["balls"]}
                      if inn["overs_log"] else None),
    }


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def _phase_split(g: dict, inn: dict) -> list[dict]:
    overs = int(g["cfg"]["overs"])
    acc = {"pp": [0, 0, 0], "mid": [0, 0, 0], "death": [0, 0, 0]}   # runs, wkts, legal balls
    for _lab, tok, runs, _b, _w, wk, over_no in inn["log"]:
        phase = ai.phase_of(int(over_no), overs)
        acc[phase][0] += runs
        acc[phase][1] += wk
        if tok not in ("wd", "nb"):
            acc[phase][2] += 1
    names = {"pp": "Powerplay", "mid": "Middle", "death": "Death"}
    out = []
    for key in ("pp", "mid", "death"):
        r, w, b = acc[key]
        if b == 0 and r == 0:
            continue
        out.append({"key": key, "label": names[key], "runs": r, "wkts": w, "balls": b, "rr": _econ(r, b)})
    return out


def _innings_stats(g: dict, inn: dict) -> dict:
    legal = max(1, inn["balls"])
    dots = inn["dots"]
    bounds = inn["fours"] + inn["sixes"]
    best_p = max(inn["partnerships"], key=lambda x: x["runs"], default=None)
    top_bat = max(inn["batters"], key=lambda b: (b["runs"], -b["balls"]), default=None)
    top_bowl = None
    for key, st in inn["bowlers"].items():
        if not st["balls"]:
            continue
        if top_bowl is None or (st["wkts"], -st["runs"]) > (top_bowl[1]["wkts"], -top_bowl[1]["runs"]):
            top_bowl = (key, st)
    return {
        "idx": inn["idx"], "bat": inn["bat"], "team": g["teams"][inn["bat"]]["name"],
        "runs": inn["runs"], "wkts": inn["wkts"], "overs": ovtxt(inn["balls"]), "rr": _econ(inn["runs"], inn["balls"]),
        "fours": inn["fours"], "sixes": inn["sixes"], "boundary_runs": inn["fours"] * 4 + inn["sixes"] * 6,
        "boundary_pct": round(100.0 * bounds / legal, 1) if inn["balls"] else 0.0,
        "dots": dots, "dot_pct": round(100.0 * dots / legal, 1) if inn["balls"] else 0.0,
        "extras": sum(inn["extras"].values()), "phases": _phase_split(g, inn),
        "best_partnership": ({"runs": best_p["runs"], "balls": best_p["balls"],
                              "a": inn["batters"][best_p["a"]]["name"], "b": inn["batters"][best_p["b"]]["name"]}
                             if best_p and best_p["runs"] else None),
        "top_bat": ({"name": top_bat["name"], "runs": top_bat["runs"], "balls": top_bat["balls"]}
                    if top_bat and top_bat["runs"] else None),
        "top_bowl": ({"name": _name(g, inn["bowl"], int(top_bowl[0])), "wkts": top_bowl[1]["wkts"],
                      "runs": top_bowl[1]["runs"], "overs": ovtxt(top_bowl[1]["balls"])} if top_bowl else None),
        "worm": _worm(inn),
        "over_runs": [{"no": o["no"], "runs": o["runs"], "wkts": o["wkts"]} for o in inn["overs_log"]],
    }


def _worm(inn: dict) -> list[list[float]]:
    pts = [[0.0, 0]]
    for o in inn["overs_log"]:
        if not o.get("partial"):
            pts.append([float(o["no"]), o["cum"]])
    if inn["balls"] % 6 or (inn["overs_log"] and inn["overs_log"][-1].get("partial")):
        pts.append([round(inn["balls"] / 6.0, 2), inn["runs"]])
    return pts


def _choice_rows(g: dict, kind: str) -> list[dict]:
    cs = (g.get("cs") or {}).get(kind) or {}
    meta = ge.MINDSET_META if kind == "bat" else ge.TACTIC_META
    rows = []
    for key, (balls, runs, wkts, bounds) in cs.items():
        info = meta.get(key, {"label": key, "emoji": ""})
        rows.append({"key": key, "label": info["label"], "emoji": info["emoji"], "balls": balls, "runs": runs,
                     "wkts": wkts, "boundaries": bounds,
                     "rpb": round(runs / balls, 2) if balls else 0.0,
                     "sr": round(runs * 100.0 / balls, 1) if balls else 0.0})
    rows.sort(key=lambda r: -r["balls"])
    return rows


def stats_block(g: dict) -> dict:
    inns = g["inn"]
    per = [_innings_stats(g, i) for i in inns]
    compare = []
    if len(per) == 2:
        a, b = per
        u, c = (a, b) if a["bat"] == "user" else (b, a)

        def row(label: str, key: str, fmt: str = "{}"):
            compare.append({"label": label, "user": fmt.format(u[key]), "ai": fmt.format(c[key]),
                            "lead": "user" if u[key] > c[key] else ("ai" if c[key] > u[key] else "even")})

        row("Runs", "runs")
        row("Wickets lost", "wkts")
        row("Run rate", "rr", "{:.2f}")
        row("Fours", "fours")
        row("Sixes", "sixes")
        row("Boundary %", "boundary_pct", "{:.1f}%")
        row("Dot ball %", "dot_pct", "{:.1f}%")
        row("Extras", "extras")
    last12 = []
    cur_inn = ge.cur(g)
    momentum = None
    if cur_inn and cur_inn["log"]:
        recent = [x for x in cur_inn["log"] if x[1] not in ("wd", "nb")][-12:]
        last12 = [x[1] for x in recent]
        runs12 = sum(x[2] for x in recent)
        wk12 = sum(x[5] for x in recent)
        momentum = {"runs": runs12, "wkts": wk12, "balls": len(recent), "tokens": last12,
                    "rr": _econ(runs12, len(recent))}
    return {"innings": per, "compare": compare, "momentum": momentum,
            "choices": {"bat": _choice_rows(g, "bat"), "bowl": _choice_rows(g, "bowl")}}


# ---------------------------------------------------------------------------
# pending action
# ---------------------------------------------------------------------------
def _meta_list(keys: list[str], table: dict) -> list[dict]:
    return [{"key": k, "label": table[k]["label"], "emoji": table[k]["emoji"], "hint": table[k]["hint"]}
            for k in keys if k in table]


def need_block(g: dict) -> dict:
    pend = ge.pending(g)
    kind = pend["kind"]
    out: dict[str, Any] = {"kind": kind}
    inn = ge.cur(g)
    if kind == "mindset":
        out["options"] = _meta_list(pend["options"], ge.MINDSET_META)
        out["free_hit"] = bool(inn["free_hit"]) if inn else False
        out["scope"] = "over" if g["cfg"]["sim"] == "over" else "ball"
    elif kind == "bowler":
        out["options"] = pend["options"]
        out["scope"] = "over"
    elif kind == "tactic":
        p = g["teams"]["user"]["xi"][pend["bowler"]]
        out["bowler"] = {"slot": p["slot"], "name": p["name"], "bw": p.get("bw"),
                         "bw_label": BW_LABELS.get(p.get("bw") or "RAM")}
        out["options"] = _meta_list(pend["options"], ge.TACTIC_META)
        out["scope"] = "over" if g["cfg"]["sim"] == "over" else "ball"
    return out


# ---------------------------------------------------------------------------
# config + full view
# ---------------------------------------------------------------------------
def config_block() -> dict:
    return {
        "difficulties": eng.public_difficulty_info(),
        "overs": [{"value": o, "label": eng.format_profile(o)["label"], "quota": eng.bowler_over_quota(o)}
                  for o in ge.OVERS_CHOICES],
        "sims": [{"key": "ball", "label": "Ball by ball", "hint": "Pick your approach on every delivery"},
                 {"key": "over", "label": "Over by over", "hint": "One choice, the whole over is simulated"}],
        "pitches": [{"key": k, **ge.PITCH_INFO[k], "tip": PITCH_TIPS[k]} for k in ge.PITCH_ORDER],
        "mindsets": [{"key": k, **v} for k, v in ge.MINDSET_META.items()],
        "tactics": [{"key": k, **v} for k, v in ge.TACTIC_META.items()],
    }


def _toss_text(g: dict) -> str | None:
    t = g.get("toss")
    if not t:
        return None
    winner = g["teams"][t["winner"]]["name"]
    if not t.get("decision"):
        return f"{winner} won the toss"
    return f"{winner} won the toss and chose to {'bat' if t['decision'] == 'bat' else 'bowl'}"


def build_view(g: dict, events: list[dict] | None = None) -> dict:
    stage = g["stage"]
    cfg = g["cfg"]
    prof = eng.difficulty_profile(cfg["difficulty"])
    pit = ge.PITCH_INFO[cfg["pitch"]]
    wp = g.get("wp") or {}
    user_pct = float(wp["user"]) if wp.get("user") is not None else 50.0
    view: dict[str, Any] = {
        "id": g["id"], "rev": g["rev"], "stage": stage, "stage_label": STAGE_LABELS.get(stage, stage),
        "cfg": {"difficulty": cfg["difficulty"], "difficulty_label": prof["label"], "stars": prof["stars"],
                "overs": cfg["overs"], "overs_label": eng.format_profile(cfg["overs"])["label"], "sim": cfg["sim"],
                "pitch": cfg["pitch"], "pitch_label": pit["label"], "pitch_emoji": pit["emoji"],
                "pitch_desc": pit["desc"], "pitch_tip": PITCH_TIPS[cfg["pitch"]],
                "random_pitch": bool(cfg.get("random_pitch")), "quota": eng.bowler_over_quota(cfg["overs"])},
        "teams": {s: {"name": g["teams"][s]["name"], "code": g["teams"][s]["code"]} for s in ("user", "ai")},
        "toss": ({**g["toss"], "text": _toss_text(g)} if g.get("toss") else None),
        "need": need_block(g),
        "wp": {"user": round(user_pct, 1), "ai": round(100.0 - user_pct, 1),
               "leader": "user" if user_pct > 52 else ("ai" if user_pct < 48 else "even"),
               "series": wp.get("series", [])[-260:], "proj": wp.get("proj"), "range": wp.get("range")},
        "feed": list(reversed(g["feed"][-16:])),
        "events": events or [],
        "result": g.get("result"),
        "balls_played": g.get("balls_played", 0),
    }
    if stage == "xi":
        team = g["teams"]["user"]
        view["xi"] = {"players": [player_card(p, i + 1) for i, p in enumerate(team["xi"])],
                      "bench": [player_card(p) for p in team["bench"]], "summary": ge.xi_summary(g),
                      "changes": int(g.get("xi_changes") or 0)}
    if stage in {"toss", "decision"}:
        team = g["teams"]["user"]
        view["xi_brief"] = ge.xi_summary(g)
    if g["inn"]:
        inn = ge.cur(g)
        view["match"] = match_block(g, inn)
        view["scorecards"] = [scorecard(g, i) for i in g["inn"]]
        view["stats"] = stats_block(g)
        view["first_innings"] = ({"team": g["teams"][g["inn"][0]["bat"]]["name"], "runs": g["inn"][0]["runs"],
                                  "wkts": g["inn"][0]["wkts"], "overs": ovtxt(g["inn"][0]["balls"]),
                                  "target": g.get("target")} if g["inn"][0]["done"] else None)
    view["lineups"] = {s: [player_card(p, i + 1) for i, p in enumerate(g["teams"][s]["xi"])]
                       for s in ("user", "ai")} if stage != "xi" else None
    return view
