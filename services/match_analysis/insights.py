"""Report-only analytics for the HTML match analysis.

Everything here is derived from what the engine already recorded (ball
telemetry + innings snapshots). Nothing feeds back into match simulation.
Every function tolerates missing data: an exited match with no balls still
produces a valid (smaller) report.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

EXTRA_KINDS = {"wide", "no_ball", "bye", "leg_bye", "legbyes", "byes", "penalty"}


def _i(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _kind(ball: dict[str, Any]) -> str:
    return str(ball.get("extra_type") or ball.get("outcome") or "").lower()


def is_wide(b: dict[str, Any]) -> bool:
    return _kind(b) in {"wide", "wide_ball", "wd"} or str(b.get("outcome")) == "wide"


def is_noball(b: dict[str, Any]) -> bool:
    return _kind(b) in {"no_ball", "noball", "nb"} or str(b.get("outcome")) == "no_ball"


def is_bye(b: dict[str, Any]) -> bool:
    return _kind(b) in {"bye", "byes", "by", "leg_bye", "legbyes", "lb"} or str(b.get("outcome")) in {"bye", "leg_bye"}


def bat_runs(b: dict[str, Any]) -> int:
    """Runs credited to the bat (wides/byes/leg-byes excluded, no-ball penalty removed)."""
    r = _i(b.get("runs"))
    if is_wide(b) or is_bye(b):
        return 0
    if is_noball(b):
        return max(0, r - 1)
    return r


def _phase(over: int, total: int) -> str:
    if total <= 1:
        return "Super Over"
    if over <= min(6, total):
        return "Powerplay"
    if over <= max(6, total - 4):
        return "Middle"
    return "Death"


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def chase_probability(first_runs: int, score: int, wickets: int, legal: int, total_overs: int) -> float:
    """Chasing-side win chance (0-100) from runs needed, balls and wickets left.

    Expected remaining runs = par rate x balls left x wicket-resource factor;
    outcome uncertainty shrinks as the innings runs out.
    """
    target = first_runs + 1
    if score >= target:
        return 100.0
    max_balls = max(1, total_overs * 6)
    left = max(0, max_balls - legal)
    if left <= 0 or wickets >= 10:
        return 0.0
    need = target - score
    par = max(6.0, min(13.0, first_runs / max(1, total_overs)))
    resource = (1.0 - wickets / 10.0) ** 0.75
    expected = par * left / 6.0 * (0.55 + 0.45 * resource) * resource ** 0.35
    sd = max(2.5, 4.2 * math.sqrt(left / 6.0))
    return max(0.5, min(99.5, _phi((expected - need) / sd) * 100.0))


def _innings_balls(snap: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in (snap.get("analysis_balls") or []) if isinstance(b, dict)]


def _team(snap: dict[str, Any], fallback: str) -> str:
    return str(snap.get("batting_team_display") or fallback)


# --------------------------------------------------------------------------- per innings
def innings_model(snap: dict[str, Any], idx: int, total_overs: int) -> dict[str, Any]:
    balls = _innings_balls(snap)
    team = _team(snap, f"Innings {idx + 1}")
    legal = sum(1 for b in balls if b.get("legal"))
    legal_snap = _i(snap.get("legal_balls"))
    legal_total = legal or legal_snap
    runs = _i(snap.get("runs")) or sum(_i(b.get("runs")) for b in balls)
    wickets = _i(snap.get("wickets")) or sum(1 for b in balls if b.get("wicket"))
    rr = runs * 6.0 / legal_total if legal_total else 0.0

    fours = sixes = dots = ext_runs = 0
    ext = Counter()
    boundary_runs = 0
    cum = wk = lg = 0
    worm: list[dict[str, Any]] = []
    fow: list[dict[str, Any]] = []
    by_phase: dict[str, dict[str, Any]] = {}
    pair_runs = pair_balls = 0
    pair_start_wk = 0
    partnerships: list[dict[str, Any]] = []
    pair_names: tuple[str, str] | None = None
    bat_tbl = defaultdict(lambda: {"balls": 0, "runs": 0, "wkts": 0, "bounds": 0, "dots": 0})
    bowl_tbl = defaultdict(lambda: {"balls": 0, "runs": 0, "wkts": 0, "bounds": 0, "dots": 0})
    per_batter = defaultdict(lambda: {"dots": 0, "balls": 0, "runs": 0, "fours": 0, "sixes": 0})
    per_bowler = defaultdict(lambda: {"dots": 0, "wides": 0, "noballs": 0, "balls": 0, "runs": 0, "wkts": 0, "overs": defaultdict(lambda: {"runs": 0, "legal": 0})})
    over_rows: dict[int, dict[str, Any]] = {}

    for b in balls:
        r = _i(b.get("runs"))
        br = bat_runs(b)
        legal_b = bool(b.get("legal"))
        ov = _i(b.get("over")) or 1
        cum += r
        if legal_b:
            lg += 1
        if is_wide(b):
            ext["wides"] += r
        elif is_noball(b):
            ext["no_balls"] += 1
        elif is_bye(b):
            ext["leg_byes" if "leg" in _kind(b) or _kind(b) == "lb" else "byes"] += r
        ext_runs += (r if (is_wide(b) or is_bye(b)) else (1 if is_noball(b) else 0))
        if br == 4:
            fours += 1; boundary_runs += 4
        elif br == 6:
            sixes += 1; boundary_runs += 6
        dot = legal_b and r == 0 and not b.get("wicket")
        if dot:
            dots += 1
        ph = _phase(ov, total_overs)
        p = by_phase.setdefault(ph, {"phase": ph, "runs": 0, "wkts": 0, "balls": 0, "dots": 0, "bounds": 0})
        p["runs"] += r; p["balls"] += 1 if legal_b else 0; p["dots"] += 1 if dot else 0
        p["bounds"] += 1 if br in (4, 6) else 0
        if b.get("wicket"):
            p["wkts"] += 1
        orow = over_rows.setdefault(ov, {"over": ov, "runs": 0, "wkts": 0, "legal": 0, "balls": []})
        orow["runs"] += r; orow["legal"] += 1 if legal_b else 0; orow["wkts"] += 1 if b.get("wicket") else 0
        orow["balls"].append(b)

        batter = str(b.get("batter") or "Batter"); bowler = str(b.get("bowler") or "Bowler")
        nonst = str(b.get("non_striker") or "Partner")
        if pair_names is None:
            pair_names = (batter, nonst)
        pair_runs += r; pair_balls += 1 if legal_b else 0
        if not is_wide(b):
            pb = per_batter[batter]
            pb["balls"] += 1 if (legal_b or is_noball(b)) else 0
            pb["runs"] += br
            pb["dots"] += 1 if (dot and not is_bye(b)) or (legal_b and br == 0 and not is_bye(b) and not b.get("wicket")) else 0
            pb["fours"] += 1 if br == 4 else 0; pb["sixes"] += 1 if br == 6 else 0
        bw = per_bowler[bowler]
        bw["balls"] += 1 if legal_b else 0
        bw["runs"] += 0 if is_bye(b) else _i(b.get("bowler_runs") if b.get("bowler_runs") is not None else r)
        bw["dots"] += 1 if dot else 0
        bw["wides"] += 1 if is_wide(b) else 0; bw["noballs"] += 1 if is_noball(b) else 0
        bo = bw["overs"][ov]
        bo["runs"] += 0 if is_bye(b) else r; bo["legal"] += 1 if legal_b else 0
        for tbl, key in ((bat_tbl, str(b.get("batting_approach") or "Balanced")), (bowl_tbl, str(b.get("bowling_plan") or "Mixed"))):
            t = tbl[key]
            t["balls"] += 1 if legal_b else 0; t["runs"] += r; t["dots"] += 1 if dot else 0
            t["bounds"] += 1 if br in (4, 6) else 0
            t["wkts"] += 1 if b.get("wicket") else 0
        if b.get("wicket"):
            wk += 1
            bw["wkts"] += 0 if str(b.get("wicket_kind") or "").lower() in {"run_out", "run out", "runout"} else 1
            fow.append({
                "wkt": wk, "score": cum, "over": f"{ov - 1}.{max(1, _i(b.get('ball')) or 1)}" if legal_b else f"{ov - 1}.{max(0, _i(b.get('ball')))}",
                "batter": batter, "kind": str(b.get("wicket_kind") or "out").replace("_", " "), "bowler": bowler,
                "text": b.get("dismissal") or "",
            })
            partnerships.append({"wkt": wk, "pair": f"{pair_names[0]} & {pair_names[1]}", "runs": pair_runs, "balls": pair_balls, "state": "broken"})
            pair_runs = pair_balls = 0; pair_names = None
        worm.append({"x": lg / 6.0, "runs": cum, "wkts": wk, "wicket": bool(b.get("wicket")), "legal": lg})
    if pair_names is not None and (pair_balls or pair_runs):
        partnerships.append({"wkt": wk + 1, "pair": f"{pair_names[0]} & {pair_names[1]}", "runs": pair_runs, "balls": pair_balls, "state": "unbroken"})

    over_list = []
    for ov in sorted(over_rows):
        row = over_rows[ov]
        row["rpo"] = row["runs"] * 6.0 / row["legal"] if row["legal"] else float(row["runs"])
        row["phase"] = _phase(ov, total_overs)
        over_list.append(row)

    phases = []
    for name in ("Powerplay", "Middle", "Death", "Super Over"):
        if name in by_phase and by_phase[name]["balls"] + by_phase[name]["runs"] > 0:
            p = by_phase[name]
            p["rr"] = p["runs"] * 6.0 / p["balls"] if p["balls"] else 0.0
            p["dot_pct"] = p["dots"] * 100.0 / p["balls"] if p["balls"] else 0.0
            phases.append(p)

    def _tbl(tbl):
        out = []
        for k, v in tbl.items():
            if v["balls"] < 1:
                continue
            out.append({"label": k, **v, "sr": v["runs"] * 100.0 / v["balls"], "rpo": v["runs"] * 6.0 / v["balls"],
                        "dot_pct": v["dots"] * 100.0 / v["balls"]})
        return sorted(out, key=lambda x: -x["balls"])

    # snapshot extras are authoritative when the engine tracked them
    snap_extras = snap.get("extras") or {}
    if snap_extras and sum(_i(v) for v in snap_extras.values()):
        extras = {
            "wides": _i(snap_extras.get("wides") or snap_extras.get("wide")),
            "no_balls": _i(snap_extras.get("no_balls") or snap_extras.get("no_ball")),
            "byes": _i(snap_extras.get("byes")),
            "leg_byes": _i(snap_extras.get("leg_byes") or snap_extras.get("legbye")),
            "penalty": _i(snap_extras.get("penalty")),
        }
    else:
        extras = {"wides": ext["wides"], "no_balls": ext["no_balls"], "byes": ext["byes"], "leg_byes": ext["leg_byes"], "penalty": 0}
    extras_total = sum(extras.values())

    # scorecard rows: snapshot is authoritative; ball telemetry adds dots / extra detail
    bat_rows = []
    for b in snap.get("batters") or []:
        balls_f = _i(b.get("balls")); runs_b = _i(b.get("runs"))
        if not balls_f and not b.get("dismissed") and not runs_b:
            continue
        extra = per_batter.get(str(b.get("name")), {})
        bat_rows.append({
            "name": b.get("name") or "Player", "runs": runs_b, "balls": balls_f, "fours": _i(b.get("fours")), "sixes": _i(b.get("sixes")),
            "sr": runs_b * 100.0 / balls_f if balls_f else 0.0, "dots": _i(extra.get("dots")) if extra else None,
            "out": bool(b.get("dismissed")), "how": b.get("dismissal_text") or ("out" if b.get("dismissed") else "not out"),
            "role": b.get("role"),
        })
    did_not_bat = [str(b.get("name")) for b in (snap.get("batters") or []) if not _i(b.get("balls")) and not b.get("dismissed") and not _i(b.get("runs"))]

    bowl_rows = []
    for b in snap.get("bowlers") or []:
        name = str(b.get("name") or "Bowler"); bl = _i(b.get("balls"))
        if not bl and not _i(b.get("runs")):
            continue
        det = per_bowler.get(name)
        maidens = _i(b.get("maidens"))
        if not maidens and det:
            maidens = sum(1 for o in det["overs"].values() if o["legal"] == 6 and o["runs"] == 0)
        bowl_rows.append({
            "name": name, "balls": bl, "runs": _i(b.get("runs")), "wkts": _i(b.get("wickets")), "maidens": maidens,
            "econ": _i(b.get("runs")) * 6.0 / bl if bl else 0.0,
            "dots": det["dots"] if det else None, "wd": det["wides"] if det else None, "nb": det["noballs"] if det else None,
        })
    if not bat_rows and per_batter:
        for n, v in per_batter.items():
            bat_rows.append({"name": n, "runs": v["runs"], "balls": v["balls"], "fours": v["fours"], "sixes": v["sixes"],
                             "sr": v["runs"] * 100.0 / v["balls"] if v["balls"] else 0.0, "dots": v["dots"], "out": False, "how": "—", "role": None})
    if not bowl_rows and per_bowler:
        for n, v in per_bowler.items():
            bowl_rows.append({"name": n, "balls": v["balls"], "runs": v["runs"], "wkts": v["wkts"], "maidens": 0,
                              "econ": v["runs"] * 6.0 / v["balls"] if v["balls"] else 0.0, "dots": v["dots"], "wd": v["wides"], "nb": v["noballs"]})

    # fall of wickets from engine snapshot when ball telemetry is absent
    if not fow:
        for k, w in enumerate(snap.get("wickets_fallen") or [], start=1):
            fow.append({"wkt": k, "score": _i(w.get("runs") or w.get("score")), "over": str(w.get("over") or "—"),
                        "batter": w.get("batter") or "Batter", "kind": str(w.get("type") or "out").replace("_", " "),
                        "bowler": w.get("bowler") or "", "text": ""})

    return {
        "idx": idx, "number": _i(snap.get("innings_number")) or idx + 1, "team": team,
        "bowling_team": snap.get("bowling_team_display") or "", "runs": runs, "wickets": wickets, "legal": legal_total,
        "over_text": snap.get("over_text") or f"{legal_total // 6}.{legal_total % 6}", "rr": rr, "target": snap.get("target"),
        "fours": fours, "sixes": sixes, "boundary_runs": boundary_runs, "dots": dots,
        "dot_pct": dots * 100.0 / legal if legal else 0.0,
        "boundary_ball_pct": (fours + sixes) * 100.0 / legal if legal else 0.0,
        "boundary_run_pct": boundary_runs * 100.0 / runs if runs else 0.0,
        "extras": extras, "extras_total": extras_total, "worm": worm, "fow": fow, "partnerships": partnerships,
        "phases": phases, "overs": over_list, "bat_approach": _tbl(bat_tbl), "bowl_plan": _tbl(bowl_tbl),
        "batters": bat_rows, "bowlers": bowl_rows, "did_not_bat": did_not_bat, "has_balls": bool(balls),
        "balls": balls,
    }


# --------------------------------------------------------------------------- match level
def _winner_and_margin(payload: dict[str, Any], inns: list[dict[str, Any]]) -> dict[str, Any]:
    term = str(payload.get("termination") or "completed")
    res = {"winner": None, "text": "", "kind": "none"}
    if len(inns) >= 2 and term == "completed":
        a, b = inns[0], inns[1]
        if b["runs"] > a["runs"]:
            left = max(0, _i(payload.get("overs_limit") or 20) * 6 - b["legal"])
            wl = max(0, 10 - b["wickets"])
            res.update(winner=b["team"], kind="chase", text=f"{b['team']} won by {wl} wicket{'s' if wl != 1 else ''}" + (f" ({left} balls left)" if left else ""))
        elif b["runs"] < a["runs"]:
            res.update(winner=a["team"], kind="defend", text=f"{a['team']} won by {a['runs'] - b['runs']} run{'s' if a['runs'] - b['runs'] != 1 else ''}")
        else:
            name = payload.get("winner_name")
            res.update(winner=name, kind="tie", text="Match tied" + (f" — {name} won the decider" if name else ""))
        return res
    if payload.get("winner_name"):
        res.update(winner=payload["winner_name"], kind="forfeit", text=f"{payload['winner_name']} won ({str(term).replace('_', ' ')})")
    else:
        res.update(text="Match ended early" if term != "completed" else "No result")
    return res


def _win_series(payload: dict[str, Any], inns: list[dict[str, Any]], total_overs: int) -> dict[str, Any]:
    out: dict[str, Any] = {"chase": [], "proj": [], "chasing": None, "first": None, "final": None}
    if not inns:
        return out
    first = inns[0]
    # first innings projected total (blend of current rate and par)
    max_balls = total_overs * 6
    par = 8.0
    for w in first["worm"]:
        if not w["legal"]:
            continue
        left = max(0, max_balls - w["legal"])
        resource = (1.0 - w["wkts"] / 10.0) ** 0.6
        rate_now = w["runs"] / w["legal"] * 6.0
        blend = w["legal"] / max_balls
        proj = w["runs"] + left / 6.0 * (rate_now * blend + par * (1 - blend)) * (0.5 + 0.5 * resource)
        out["proj"].append({"x": w["legal"] / 6.0, "v": round(proj, 1)})
    if len(inns) > 1 and inns[1]["worm"]:
        ch = inns[1]
        out["chasing"] = ch["team"]; out["first"] = first["team"]
        target_base = first["runs"]
        for w in ch["worm"]:
            p = chase_probability(target_base, w["runs"], w["wkts"], w["legal"], total_overs)
            out["chase"].append({"x": w["legal"] / 6.0, "p": round(p, 1), "wicket": w["wicket"], "legal": w["legal"], "runs": w["runs"], "wkts": w["wkts"]})
        if out["chase"] and str(payload.get("termination") or "completed") == "completed":
            out["chase"][-1]["p"] = 100.0 if ch["runs"] > first["runs"] else (0.0 if ch["runs"] < first["runs"] else 50.0)
        out["final"] = out["chase"][-1]["p"] if out["chase"] else None
    return out


def _moments(inns: list[dict[str, Any]], win: dict[str, Any]) -> list[dict[str, Any]]:
    moments: list[dict[str, Any]] = []
    for inn in inns:
        for f in inn["fow"]:
            moments.append({"kind": "wicket", "innings": inn["number"], "over": f["over"], "team": inn["team"], "score": f"{f['score']}/{f['wkt']}",
                            "title": f"{f['batter']} {f['kind']}", "weight": 3.0 + (1.0 if f["wkt"] <= 3 else 0.0)})
        for o in inn["overs"]:
            if o["runs"] >= 15:
                moments.append({"kind": "over", "innings": inn["number"], "over": str(o["over"]), "team": inn["team"], "score": f"{o['runs']} runs",
                                "title": f"Big over: {o['runs']} off over {o['over']}", "weight": o["runs"] / 4.0})
            if o["legal"] >= 6 and o["runs"] == 0:
                moments.append({"kind": "maiden", "innings": inn["number"], "over": str(o["over"]), "team": inn["team"], "score": "0 runs",
                                "title": f"Maiden over (over {o['over']})", "weight": 2.5})
        streak = best = 0
        end = 0
        for k, b in enumerate(inn["balls"]):
            if bat_runs(b) in (4, 6):
                streak += 1
                if streak > best:
                    best, end = streak, k
            else:
                streak = 0
        if best >= 3:
            b = inn["balls"][end]
            moments.append({"kind": "streak", "innings": inn["number"], "over": f"{_i(b.get('over')) - 1}.{_i(b.get('ball'))}", "team": inn["team"],
                            "score": f"{best} in a row", "title": f"{best} consecutive boundaries", "weight": 2.0 + best})
    # chase swings
    ch = win.get("chase") or []
    for a, b in zip(ch, ch[1:]):
        d = b["p"] - a["p"]
        if abs(d) >= 8 and b["legal"] < len(ch):
            moments.append({"kind": "swing", "innings": 2, "over": f"{(b['legal'] - 1) // 6}.{(b['legal'] - 1) % 6 + 1}", "team": win.get("chasing") or "",
                            "score": f"{b['runs']}/{b['wkts']}", "title": f"Win chance {'up' if d > 0 else 'down'} {abs(d):.0f}% for {win.get('chasing')}", "weight": abs(d) / 3.0})
    moments.sort(key=lambda m: -m["weight"])
    seen, out = set(), []
    for m in moments:
        key = (m["innings"], m["over"], m["kind"])
        if key in seen:
            continue
        seen.add(key); out.append(m)
        if len(out) >= 10:
            break
    return out


def _impact(inns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for inn in inns:
        par = max(6.0, inn["rr"])
        for b in inn["batters"]:
            if not b["balls"]:
                continue
            v = b["runs"] * 0.9 + b["fours"] * 0.6 + b["sixes"] * 1.4 + (b["sr"] - 110) / 12.0 * min(1.0, b["balls"] / 12.0)
            rows.append({"name": b["name"], "team": inn["team"], "kind": "Bat", "line": f"{b['runs']} ({b['balls']})", "impact": max(0.0, v), "runs": b["runs"], "balls": b["balls"], "wkts": 0})
        for b in inn["bowlers"]:
            if not b["balls"]:
                continue
            v = b["wkts"] * 24.0 + max(0.0, 9.0 - b["econ"]) * b["balls"] / 6.0 * 1.6 + b["maidens"] * 6.0
            rows.append({"name": b["name"], "team": inn["bowling_team"] or "", "kind": "Bowl", "line": f"{b['wkts']}/{b['runs']} ({b['balls'] // 6}.{b['balls'] % 6})", "impact": max(0.0, v), "runs": 0, "balls": b["balls"], "wkts": b["wkts"], "econ": b["econ"]})
    top = max((r["impact"] for r in rows), default=1.0) or 1.0
    for r in rows:
        r["impact"] = round(r["impact"] * 100.0 / top, 1)
    rows.sort(key=lambda r: -r["impact"])
    return rows


def _insights(inns: list[dict[str, Any]], win: dict[str, Any], res: dict[str, Any], impact: list[dict[str, Any]]) -> list[str]:
    ins: list[str] = []
    if len(inns) >= 2:
        a, b = inns[0], inns[1]
        if b["has_balls"] and a["has_balls"]:
            d = b["dot_pct"] - a["dot_pct"]
            ins.append(f"Dot-ball pressure: {a['team']} played {a['dot_pct']:.0f}% dots, {b['team']} {b['dot_pct']:.0f}%"
                       + (f" — {b['team']} left more balls unscored." if d > 3 else (f" — {a['team']} left more balls unscored." if d < -3 else " — evenly matched.")))
            ins.append(f"Boundary power: {a['team']} hit {a['fours']}×4 and {a['sixes']}×6 ({a['boundary_run_pct']:.0f}% of runs); {b['team']} {b['fours']}×4 and {b['sixes']}×6 ({b['boundary_run_pct']:.0f}%).")
    for inn in inns:
        if inn["phases"]:
            fast = max(inn["phases"], key=lambda p: p["rr"])
            slow = min(inn["phases"], key=lambda p: p["rr"])
            if fast is not slow:
                ins.append(f"{inn['team']} scored fastest in the {fast['phase']} ({fast['rr']:.1f} rpo) and slowest in the {slow['phase']} ({slow['rr']:.1f} rpo).")
        used = [r for r in inn["bat_approach"] if r["balls"] >= 6]
        if used:
            best = max(used, key=lambda r: r["sr"])
            ins.append(f"{inn['team']}'s most productive approach was {best['label']}: {best['runs']} off {best['balls']} (SR {best['sr']:.0f}).")
            risky = max(used, key=lambda r: r["wkts"] / r["balls"])
            if risky["wkts"]:
                ins.append(f"{risky['label']} cost {inn['team']} {risky['wkts']} wicket{'s' if risky['wkts'] != 1 else ''} in {risky['balls']} balls.")
        usedb = [r for r in inn["bowl_plan"] if r["balls"] >= 6]
        if usedb:
            best = min(usedb, key=lambda r: r["rpo"] - 3.0 * r["wkts"] * 6 / r["balls"])
            ins.append(f"Against {inn['team']}, the bowling plan that worked best was {best['label']}: {best['runs']} runs and {best['wkts']} wkt(s) from {best['balls']} balls.")
        if inn["partnerships"]:
            bp = max(inn["partnerships"], key=lambda p: p["runs"])
            if bp["runs"] >= 20:
                ins.append(f"Best partnership for {inn['team']}: {bp['runs']} ({bp['balls']}) by {bp['pair']}.")
    ch = win.get("chase") or []
    if len(ch) > 4:
        sw = max(range(1, len(ch)), key=lambda i: abs(ch[i]["p"] - ch[i - 1]["p"]))
        d = ch[sw]["p"] - ch[sw - 1]["p"]
        ins.append(f"Biggest swing: {abs(d):.0f}% for {win['chasing']} around ball {ch[sw]['legal']} of the chase ({'in their favour' if d > 0 else 'against them'}).")
        peak = max(ch, key=lambda p: p["p"]); low = min(ch, key=lambda p: p["p"])
        ins.append(f"{win['chasing']}'s win chance ranged from {low['p']:.0f}% to {peak['p']:.0f}% during the chase.")
    if impact:
        top = impact[0]
        ins.append(f"Standout performer: {top['name']} ({top['team']}) — {top['line']}.")
    return ins[:12]


def _narrative(payload: dict[str, Any], inns: list[dict[str, Any]], res: dict[str, Any], win: dict[str, Any], impact: list[dict[str, Any]]) -> list[str]:
    paras: list[str] = []
    if not inns:
        reason = payload.get("reason") or "The match ended before any innings telemetry was recorded."
        return [f"{reason}"]
    a = inns[0]
    toss = payload.get("toss_winner_name")
    dec = payload.get("decision")
    opening = f"{toss} won the toss and chose to {str(dec).lower()}. " if toss and dec else ""
    paras.append(f"{opening}{a['team']} posted {a['runs']}/{a['wickets']} in {a['over_text']} overs ({a['rr']:.2f} rpo)"
                 + (f", with {a['fours']} fours and {a['sixes']} sixes." if a["has_balls"] else "."))
    if a["phases"]:
        parts = [f"{p['phase']} {p['runs']}/{p['wkts']}" for p in a["phases"]]
        paras[-1] += " Phase split: " + ", ".join(parts) + "."
    if len(inns) > 1:
        b = inns[1]
        line = f"{b['team']} replied with {b['runs']}/{b['wickets']} in {b['over_text']} overs ({b['rr']:.2f} rpo), chasing {a['runs'] + 1}."
        ch = win.get("chase") or []
        if ch:
            mid = ch[len(ch) // 2]
            line += f" At the halfway point of the chase their win chance stood at {mid['p']:.0f}%."
        paras.append(line)
    if res.get("text"):
        paras.append(f"Result: {res['text']}.")
    if str(payload.get("termination") or "completed") != "completed" and payload.get("reason"):
        paras.append(f"Ended early: {payload['reason']}")
    if impact:
        t = impact[0]
        paras.append(f"{t['name']} had the biggest impact ({t['line']}).")
    return paras


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    total = _i(payload.get("overs_limit") or 20) or 20
    if str(payload.get("engine") or "").upper() == "PLAYSO":
        total = 1
    inns = [innings_model(s, i, total) for i, s in enumerate(payload.get("innings") or [])]
    res = _winner_and_margin(payload, inns)
    win = _win_series(payload, inns, total)
    impact = _impact(inns)
    moments = _moments(inns, win)
    potm = payload.get("potm") or {}
    potm_row = next((r for r in impact if r["name"] == potm.get("name")), None)
    # keep POTM consistent with the impact board when both exist
    if impact and (not potm_row or impact[0]["impact"] > potm_row["impact"] + 0.01):
        top = impact[0]
        potm = {"name": top["name"], "line": top["line"], "team": top["team"]}
    elif potm_row:
        potm = {"name": potm_row["name"], "line": potm_row["line"], "team": potm_row["team"]}
    return {
        "total_overs": total, "innings": inns, "result": res, "win": win, "impact": impact[:12], "moments": moments,
        "insights": _insights(inns, win, res, impact), "narrative": _narrative(payload, inns, res, win, impact), "potm": potm,
    }
