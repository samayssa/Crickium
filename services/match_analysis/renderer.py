"""Self-contained HTML match analysis (works offline, mobile first)."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from services.match_analysis.insights import bat_runs, build_report, is_noball, is_wide

ROOT = Path(__file__).resolve().parent
THEME_PATH = ROOT / "theme.css"
TEMPLATE_PATH = ROOT / "template.html"
COL = ["#2fe6c0", "#ff7aa0"]


def esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _i(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def num(v: Any, d: int = 0) -> str:
    try:
        x = float(v or 0)
    except Exception:
        x = 0.0
    return f"{x:,.{d}f}" if d else f"{int(round(x)):,}"


def ov(balls: int) -> str:
    balls = _i(balls)
    return f"{balls // 6}.{balls % 6}"


def _status(v: Any) -> str:
    return str(v or "completed").replace("_", " ").title()


def _empty(msg: str) -> str:
    return f"<div class='note'>{esc(msg)}</div>"


def _table(head: list[tuple[str, bool]], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f"<th{' class=num' if n else ''}>{esc(h)}</th>" for h, n in head)
    body = "".join("<tr>" + "".join(f"<td{' class=num' if head[k][1] else ''}>{c}</td>" for k, c in enumerate(r)) + "</tr>" for r in rows)
    return f"<div class='scroll'><table class='{cls}'><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>"


def _legend(items: list[tuple[str, str]]) -> str:
    return "<div class='legend'>" + "".join(f"<span><i style='background:{c}'></i>{esc(t)}</span>" for t, c in items) + "</div>"


# ---------------------------------------------------------------- charts
def _axes(w, h, l, r, t, b, ymax, ticks=4, fmt=lambda v: f"{v:.0f}"):
    out = []
    for k in range(ticks + 1):
        v = ymax * k / ticks
        y = t + (1 - k / ticks) * (h - t - b)
        out.append(f"<line class='gl' x1='{l}' x2='{w - r}' y1='{y:.1f}' y2='{y:.1f}'/><text class='ax' x='{l - 6}' y='{y + 4:.1f}' text-anchor='end'>{fmt(v)}</text>")
    return "".join(out)


def _worm(rep: dict[str, Any]) -> str:
    inns = [i for i in rep["innings"] if i["worm"]]
    if not inns:
        return _empty("No ball-by-ball telemetry was recorded, so the worm is unavailable.")
    W, H, L, R, T, B = 760, 280, 44, 16, 16, 30
    total = rep["total_overs"]
    xmax = max(1.0, float(total), max(w["x"] for i in inns for w in i["worm"]))
    ymax = max(10, max(w["runs"] for i in inns for w in i["worm"]))
    ymax = int((ymax // 20 + 1) * 20)
    X = lambda x: L + x / xmax * (W - L - R)
    Y = lambda y: T + (1 - y / ymax) * (H - T - B)
    out = [_axes(W, H, L, R, T, B, ymax)]
    step = 1 if xmax <= 10 else (2 if xmax <= 20 else 5)
    for o in range(0, int(xmax) + 1, step):
        out.append(f"<text class='ax' x='{X(o):.1f}' y='{H - 8}' text-anchor='middle'>{o}</text>")
    for k, inn in enumerate(inns):
        pts = [(0, 0)] + [(w["x"], w["runs"]) for w in inn["worm"]]
        out.append(f"<polyline fill='none' stroke='{COL[k % 2]}' stroke-width='2.8' stroke-linejoin='round' stroke-linecap='round' points='" + " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts) + "'/>")
        for w in inn["worm"]:
            if w["wicket"]:
                out.append(f"<circle cx='{X(w['x']):.1f}' cy='{Y(w['runs']):.1f}' r='4.6' fill='{COL[k % 2]}' stroke='#070b14' stroke-width='2'><title>{esc(inn['team'])}: wicket at {w['runs']}/{w['wkts']}</title></circle>")
    # target line
    if len(inns) > 1:
        tgt = inns[0]["runs"] + 1
        if tgt <= ymax:
            out.append(f"<line x1='{L}' x2='{W - R}' y1='{Y(tgt):.1f}' y2='{Y(tgt):.1f}' stroke='#ffc857' stroke-dasharray='5 5' stroke-width='1.4'/><text class='ax' x='{W - R}' y='{Y(tgt) - 5:.1f}' text-anchor='end' fill='#ffc857'>Target {tgt}</text>")
    svg = f"<svg class='chart' viewBox='0 0 {W} {H}' role='img' aria-label='Run progression'>{''.join(out)}</svg>"
    return svg + _legend([(i["team"], COL[k % 2]) for k, i in enumerate(inns)] + [("● wicket", "#8aa0bf")])


def _win_chart(rep: dict[str, Any]) -> str:
    win = rep["win"]; ch = win.get("chase") or []
    if len(ch) < 2:
        return _empty("Win probability needs a completed chase with ball telemetry.")
    W, H, L, R, T, B = 760, 250, 44, 16, 16, 28
    xmax = max(1, ch[-1]["legal"])
    X = lambda x: L + x / xmax * (W - L - R)
    Y = lambda p: T + (1 - p / 100.0) * (H - T - B)
    pts = [(0, 50.0)] + [(c["legal"], c["p"]) for c in ch]
    line = " ".join(f"{X(x):.1f},{Y(p):.1f}" for x, p in pts)
    area = f"{X(0):.1f},{Y(50):.1f} " + line + f" {X(pts[-1][0]):.1f},{Y(50):.1f}"
    out = [_axes(W, H, L, R, T, B, 100, 4, lambda v: f"{v:.0f}%")]
    out.append(f"<line x1='{L}' x2='{W - R}' y1='{Y(50):.1f}' y2='{Y(50):.1f}' stroke='#8aa0bf' stroke-width='1.2'/>")
    out.append(f"<polygon points='{area}' fill='{COL[1]}' opacity='.12'/>")
    out.append(f"<polyline fill='none' stroke='{COL[1]}' stroke-width='2.8' stroke-linejoin='round' points='{line}'/>")
    for c in ch:
        if c["wicket"]:
            out.append(f"<circle cx='{X(c['legal']):.1f}' cy='{Y(c['p']):.1f}' r='3.8' fill='#fff' stroke='{COL[1]}' stroke-width='2'><title>Wicket at {c['runs']}/{c['wkts']} → {c['p']:.0f}%</title></circle>")
    for o in range(0, xmax // 6 + 1, 2 if xmax > 60 else 1):
        out.append(f"<text class='ax' x='{X(o * 6):.1f}' y='{H - 8}' text-anchor='middle'>{o}</text>")
    note = f"<p class='note'>Line = {esc(win['chasing'])} win chance after every ball of the chase (above 50% favours the chasing side). White dots are wickets. Estimated from runs needed, balls and wickets left — not the engine's own probability.</p>"
    return f"<svg class='chart' viewBox='0 0 {W} {H}' role='img' aria-label='Win probability'>{''.join(out)}</svg>" + note


def _over_bars(rep: dict[str, Any]) -> str:
    inns = [i for i in rep["innings"] if i["overs"]]
    if not inns:
        return _empty("No over-by-over telemetry was recorded for this match.")
    out = []
    for k, inn in enumerate(inns):
        rows = inn["overs"]
        W, H, L, R, T, B = 760, 190, 30, 10, 20, 26
        ymax = max(10, max(r["runs"] for r in rows))
        n = max(len(rows), rep["total_overs"] if rep["total_overs"] <= 20 else len(rows))
        bw = (W - L - R) / n
        bars = [_axes(W, H, L, R, T, B, ymax, 2)]
        for j, r in enumerate(rows):
            x = L + (r["over"] - 1) * bw + 2
            h = r["runs"] / ymax * (H - T - B)
            fill = "#ff5d7a" if r["wkts"] else COL[k % 2]
            bars.append(f"<rect x='{x:.1f}' y='{H - B - h:.1f}' width='{max(4, bw - 4):.1f}' height='{max(1, h):.1f}' rx='3' fill='{fill}' opacity='.92'><title>Over {r['over']}: {r['runs']} runs, {r['wkts']} wkt</title></rect>")
            bars.append(f"<text class='ax' x='{x + (bw - 4) / 2:.1f}' y='{H - B - h - 4:.1f}' text-anchor='middle' fill='#eef4ff'>{r['runs']}</text>")
            bars.append(f"<text class='ax' x='{x + (bw - 4) / 2:.1f}' y='{H - 8}' text-anchor='middle'>{r['over']}</text>")
        out.append(f"<h3>{esc(inn['team'])} · runs per over</h3><svg class='chart' viewBox='0 0 {W} {H}'>{''.join(bars)}</svg>")
    return "".join(out) + _legend([("Wicket over", "#ff5d7a")]) + "<p class='note'>Bars show runs conceded in each over; red bars contain at least one wicket.</p>"


def _rr_compare(rep: dict[str, Any]) -> str:
    inns = [i for i in rep["innings"] if i["overs"]]
    if len(inns) < 2:
        return ""
    W, H, L, R, T, B = 760, 220, 44, 16, 16, 28
    cums = []
    for inn in inns:
        c = []; legal = runs = 0
        for r in inn["overs"]:
            legal += r["legal"]; runs += r["runs"]
            c.append((r["over"], runs * 6.0 / legal if legal else 0.0))
        cums.append(c)
    xmax = max(o for c in cums for o, _ in c)
    ymax = max(8.0, max(v for c in cums for _, v in c)); ymax = float(int(ymax) + 2)
    X = lambda x: L + (x - 1) / max(1, xmax - 1) * (W - L - R)
    Y = lambda y: T + (1 - y / ymax) * (H - T - B)
    out = [_axes(W, H, L, R, T, B, ymax, 4, lambda v: f"{v:.0f}")]
    for k, c in enumerate(cums):
        out.append(f"<polyline fill='none' stroke='{COL[k]}' stroke-width='2.6' stroke-linejoin='round' points='" + " ".join(f"{X(o):.1f},{Y(v):.1f}" for o, v in c) + "'/>")
    for o in range(1, xmax + 1, 1 if xmax <= 10 else 2):
        out.append(f"<text class='ax' x='{X(o):.1f}' y='{H - 8}' text-anchor='middle'>{o}</text>")
    return "<h3>Cumulative run rate</h3><svg class='chart' viewBox='0 0 %d %d'>%s</svg>" % (W, H, "".join(out)) + _legend([(i["team"], COL[k]) for k, i in enumerate(inns)])


def _balls(balls: list[dict[str, Any]]) -> str:
    out = []
    for b in balls:
        if b.get("wicket"):
            cls, lab = "w", "W"
        elif is_wide(b):
            cls, lab = "x", f"{_i(b.get('runs')) if _i(b.get('runs')) > 1 else ''}wd"
        elif is_noball(b):
            cls, lab = "x", "nb"
        elif bat_runs(b) == 6:
            cls, lab = "six", "6"
        elif bat_runs(b) == 4:
            cls, lab = "four", "4"
        elif _i(b.get("runs")) == 0:
            cls, lab = "dot", "•"
        else:
            cls, lab = "r", str(_i(b.get("runs")))
        tip = f"{b.get('batter') or ''} v {b.get('bowler') or ''}"
        out.append(f"<span class='ball {cls}' title='{esc(tip)}'>{esc(lab)}</span>")
    return "".join(out)


# ---------------------------------------------------------------- sections
def _hero(payload: dict[str, Any], rep: dict[str, Any]) -> str:
    inns = rep["innings"]; res = rep["result"]
    players = payload.get("players") or []
    def box(k: int) -> str:
        if k < len(inns):
            i = inns[k]
            return (f"<div class='team t{k}'><span class='lab'>Innings {k + 1}</span><b class='tn'>{esc(i['team'])}</b>"
                    f"<div class='sc'>{i['runs']}<small>/{i['wickets']}</small></div><span class='ov'>{esc(i['over_text'])} overs · RR {i['rr']:.2f}</span></div>")
        fallback = (players[k].get("first_name") if len(players) > k else f"Team {k + 1}")
        return f"<div class='team t{k}'><span class='lab'>Innings {k + 1}</span><b class='tn'>{esc(fallback)}</b><div class='sc'>—</div><span class='ov'>did not bat</span></div>"
    term = _status(payload.get("termination"))
    result = res["text"] or ("Match ended early" if payload.get("termination") != "completed" else "Result not recorded")
    chips = [
        ("📍", payload.get("stadium")), ("🧱", f"{payload.get('pitch')} pitch"), ("🌤", payload.get("weather")),
        ("🪙", f"{payload.get('toss_winner_name')} won toss · {payload.get('decision')}" if payload.get("toss_winner_name") else None),
        ("🏏", f"{payload.get('format') or 'T20'} · {rep['total_overs']} over{'s' if rep['total_overs'] != 1 else ''}"),
        ("🆔", f"Match #{num(payload.get('match_number') or payload.get('match_id'))}"),
    ]
    chip_html = "".join(f"<span class='chip'>{e} {esc(t)}</span>" for e, t in chips if t and "Not recorded" not in str(t))
    first = esc(inns[0]["team"]) if inns else esc(players[0].get("first_name") if players else "Player 1")
    second = esc(inns[1]["team"]) if len(inns) > 1 else esc(players[1].get("first_name") if len(players) > 1 else "Player 2")
    potm = rep["potm"]
    potm_html = f"<div class='potm'>⭐ <b>Player of the Match</b> {esc(potm.get('name'))} <span>{esc(potm.get('line') or '')}</span></div>" if potm and potm.get("name") not in (None, "—") else ""
    return (f"<header class='hero'><div class='eyebrow'>CRICKIUM · MATCH ANALYSIS · {esc(payload.get('game_name') or 'GAME').upper()}</div>"
            f"<h1>{first} <em>vs</em> {second}</h1>"
            f"<div class='teams'>{box(0)}<div class='vs'>VS</div>{box(1)}</div>"
            f"<div class='result'>🏆 {esc(result)}<small>{esc(term)}{(' · ' + esc(payload.get('reason'))) if payload.get('reason') else ''}</small></div>"
            f"{potm_html}<div class='chips'>{chip_html}</div></header>")


def _nav(rep: dict[str, Any]) -> str:
    items = [("report", "Report"), ("insights", "Insights"), ("worm", "Progress"), ("win", "Win %"), ("h2h", "Head to head"), ("phases", "Phases"),
             ("cards", "Scorecards"), ("wkts", "Wickets"), ("tactics", "Tactics"), ("moments", "Key moments"), ("impact", "Impact"), ("bbb", "Ball by ball")]
    return "<nav class='nav'>" + "".join(f"<a href='#{a}'>{t}</a>" for a, t in items) + "</nav>"


def _kpis(rep: dict[str, Any]) -> str:
    inns = rep["innings"]
    if not inns:
        return ""
    tiles = []
    for k, i in enumerate(inns):
        tiles.append(f"<div class='kpi'><b style='color:{COL[k % 2]}'>{i['runs']}/{i['wickets']}</b><span>{esc(i['team'])}</span></div>")
    tot_runs = sum(i["runs"] for i in inns); tot_legal = sum(i["legal"] for i in inns)
    tiles.append(f"<div class='kpi'><b>{(tot_runs * 6.0 / tot_legal) if tot_legal else 0:.2f}</b><span>Match run rate</span></div>")
    tiles.append(f"<div class='kpi'><b>{sum(i['fours'] for i in inns)} / {sum(i['sixes'] for i in inns)}</b><span>Fours / Sixes</span></div>")
    tiles.append(f"<div class='kpi'><b>{sum(i['wickets'] for i in inns)}</b><span>Wickets</span></div>")
    tiles.append(f"<div class='kpi'><b>{sum(i['extras_total'] for i in inns)}</b><span>Extras</span></div>")
    return "<div class='kpis'>" + "".join(tiles) + "</div>"


def _h2h(rep: dict[str, Any]) -> str:
    inns = rep["innings"]
    if len(inns) < 2:
        return _empty("Head-to-head needs two innings.")
    a, b = inns[0], inns[1]
    def best(i):
        t = max(i["batters"], key=lambda x: x["runs"], default=None)
        return f"{t['name']} {t['runs']} ({t['balls']})" if t else "—"
    def bestb(i):
        t = max(i["bowlers"], key=lambda x: (x["wkts"], -x["runs"]), default=None)
        return f"{t['name']} {t['wkts']}/{t['runs']}" if t else "—"
    def bp(i):
        t = max(i["partnerships"], key=lambda x: x["runs"], default=None)
        return f"{t['runs']} ({t['balls']})" if t else "—"
    def maxover(i):
        t = max(i["overs"], key=lambda x: x["runs"], default=None)
        return f"{t['runs']} (over {t['over']})" if t else "—"
    rows = [
        ("Score", f"{a['runs']}/{a['wickets']}", f"{b['runs']}/{b['wickets']}"),
        ("Overs", a["over_text"], b["over_text"]),
        ("Run rate", f"{a['rr']:.2f}", f"{b['rr']:.2f}"),
        ("Fours", a["fours"], b["fours"]), ("Sixes", a["sixes"], b["sixes"]),
        ("Boundary runs %", f"{a['boundary_run_pct']:.0f}%", f"{b['boundary_run_pct']:.0f}%"),
        ("Dot-ball %", f"{a['dot_pct']:.0f}%", f"{b['dot_pct']:.0f}%"),
        ("Extras", a["extras_total"], b["extras_total"]),
        ("Top batter", best(a), best(b)), ("Top bowler (v them)", bestb(b), bestb(a)),
        ("Best partnership", bp(a), bp(b)), ("Best over", maxover(a), maxover(b)),
    ]
    body = "".join(f"<tr><td class='h0'>{esc(r[1])}</td><td class='mid'>{esc(r[0])}</td><td class='h1'>{esc(r[2])}</td></tr>" for r in rows)
    return f"<div class='scroll'><table class='h2h'><thead><tr><th class='h0'>{esc(a['team'])}</th><th></th><th class='h1'>{esc(b['team'])}</th></tr></thead><tbody>{body}</tbody></table></div>"


def _phases(rep: dict[str, Any]) -> str:
    out = []
    for k, inn in enumerate(rep["innings"]):
        if not inn["phases"]:
            continue
        rows = [[f"<span class='badge'>{esc(p['phase'])}</span>", f"{p['runs']}/{p['wkts']}", ov(p["balls"]), f"{p['rr']:.2f}", f"{p['dot_pct']:.0f}%", num(p["bounds"])] for p in inn["phases"]]
        out.append(f"<div class='card'><h3 style='color:{COL[k % 2]}'>{esc(inn['team'])}</h3>" + _table([("Phase", False), ("Score", True), ("Overs", True), ("RR", True), ("Dot%", True), ("4s/6s", True)], rows) + "</div>")
    return "<div class='duo'>" + "".join(out) + "</div>" if out else _empty("No phase telemetry recorded.")


def _scorecard(inn: dict[str, Any], k: int) -> str:
    bat = []
    for b in inn["batters"]:
        how = "not out" if not b["out"] else str(b["how"])
        bat.append([f"<b>{esc(b['name'])}</b><small>{esc(how)}</small>", f"<b>{b['runs']}</b>", str(b["balls"]), str(b["fours"]), str(b["sixes"]),
                    "—" if b["dots"] is None else str(b["dots"]), f"{b['sr']:.1f}"])
    ex = inn["extras"]
    ex_txt = f"wd {ex['wides']}, nb {ex['no_balls']}, b {ex['byes']}, lb {ex['leg_byes']}" + (f", pen {ex['penalty']}" if ex["penalty"] else "")
    bat.append([f"<b>Extras</b><small>{esc(ex_txt)}</small>", f"<b>{inn['extras_total']}</b>", "", "", "", "", ""])
    bat.append([f"<b>Total</b><small>{esc(inn['over_text'])} overs · RR {inn['rr']:.2f}</small>", f"<b>{inn['runs']}/{inn['wickets']}</b>", "", "", "", "", ""])
    bowl = [[f"<b>{esc(b['name'])}</b>", ov(b["balls"]), str(b["maidens"]), str(b["runs"]), f"<b>{b['wkts']}</b>", "—" if b["dots"] is None else str(b["dots"]),
             "—" if b["wd"] is None else f"{b['wd']}/{b['nb']}", f"{b['econ']:.2f}"] for b in inn["bowlers"]]
    dnb = f"<p class='note'><b>Did not bat:</b> {', '.join(esc(n) for n in inn['did_not_bat'])}</p>" if inn["did_not_bat"] else ""
    return (f"<div class='card'><h3 style='color:{COL[k % 2]}'>{esc(inn['team'])} <span>{inn['runs']}/{inn['wickets']} ({esc(inn['over_text'])})</span></h3>"
            + (_table([("Batter", False), ("R", True), ("B", True), ("4s", True), ("6s", True), ("Dots", True), ("SR", True)], bat, "card-t") if inn["batters"] else _empty("No batting data."))
            + dnb + f"<h4>Bowling · {esc(inn['bowling_team'])}</h4>"
            + (_table([("Bowler", False), ("O", True), ("M", True), ("R", True), ("W", True), ("Dots", True), ("WD/NB", True), ("Econ", True)], bowl, "card-t") if bowl else _empty("No bowling data.")) + "</div>")


def _wickets(rep: dict[str, Any]) -> str:
    out = []
    for k, inn in enumerate(rep["innings"]):
        fow = "".join(f"<span class='fow'><b>{f['score']}-{f['wkt']}</b> {esc(f['batter'])} <small>{esc(f['kind'])} · ov {esc(f['over'])}</small></span>" for f in inn["fow"]) or "<span class='muted'>No wickets fell.</span>"
        pr = [[esc(p["pair"]), str(p["runs"]), str(p["balls"]), f"{p['runs'] * 6.0 / p['balls']:.1f}" if p["balls"] else "—", esc(p["state"])] for p in inn["partnerships"]]
        part = _table([("Partnership", False), ("Runs", True), ("Balls", True), ("RR", True), ("State", False)], pr) if pr else _empty("No partnership telemetry.")
        out.append(f"<div class='card'><h3 style='color:{COL[k % 2]}'>{esc(inn['team'])}</h3><h4>Fall of wickets</h4><div class='fows'>{fow}</div><h4>Partnerships</h4>{part}</div>")
    return "<div class='duo'>" + "".join(out) + "</div>" if out else _empty("No wicket data recorded.")


def _tactics(rep: dict[str, Any]) -> str:
    out = []
    for k, inn in enumerate(rep["innings"]):
        if not inn["bat_approach"] and not inn["bowl_plan"]:
            continue
        ba = [[f"<b>{esc(r['label'])}</b>", str(r["balls"]), str(r["runs"]), str(r["wkts"]), str(r["bounds"]), f"{r['sr']:.0f}", f"{r['dot_pct']:.0f}%"] for r in inn["bat_approach"]]
        bp = [[f"<b>{esc(r['label'])}</b>", str(r["balls"]), str(r["runs"]), str(r["wkts"]), str(r["bounds"]), f"{r['rpo']:.1f}", f"{r['dot_pct']:.0f}%"] for r in inn["bowl_plan"]]
        out.append(f"<div class='card'><h3 style='color:{COL[k % 2]}'>{esc(inn['team'])} batting</h3><h4>Batting approach</h4>"
                   + _table([("Approach", False), ("Balls", True), ("Runs", True), ("Wkts", True), ("4s/6s", True), ("SR", True), ("Dot%", True)], ba)
                   + f"<h4>Bowling plan faced</h4>" + _table([("Plan", False), ("Balls", True), ("Runs", True), ("Wkts", True), ("4s/6s", True), ("RPO", True), ("Dot%", True)], bp) + "</div>")
    return "<div class='duo'>" + "".join(out) + "</div>" if out else _empty("Tactical choices were not recorded for this match.")


def _moments(rep: dict[str, Any]) -> str:
    ms = rep["moments"]
    if not ms:
        return _empty("No standout moments crossed the threshold.")
    icon = {"wicket": "🔴", "over": "💥", "maiden": "🧊", "streak": "🔥", "swing": "📈"}
    return "".join(f"<div class='mom {m['kind']}'><span class='mi'>{icon.get(m['kind'], '•')}</span><div><b>{esc(m['title'])}</b>"
                   f"<small>{esc(m['team'])} · Inn {m['innings']} · over {esc(m['over'])} · {esc(m['score'])}</small></div></div>" for m in ms)


def _impact(rep: dict[str, Any]) -> str:
    rows = [[f"<b>{esc(r['name'])}</b><small>{esc(r['team'])}</small>", esc(r["kind"]), esc(r["line"]),
             f"<div class='meter'><i style='width:{min(100, r['impact']):.0f}%'></i></div><span>{r['impact']:.0f}</span>"] for r in rep["impact"]]
    if not rows:
        return _empty("No impact data available.")
    return _table([("Player", False), ("Role", False), ("Output", True), ("Impact", False)], rows, "impact") + "<p class='note'>Impact index (0–100) is a report-only composite of runs, boundaries and strike rate for batters, and wickets, economy and maidens for bowlers.</p>"


def _bbb(rep: dict[str, Any]) -> str:
    out = []
    for k, inn in enumerate(rep["innings"]):
        if not inn["overs"]:
            continue
        rows = []
        for o in inn["overs"]:
            bowler = next((str(b.get("bowler")) for b in o["balls"] if b.get("bowler")), "—")
            wk_txt = f" · {o['wkts']} wkt" if o['wkts'] else ''
            rows.append(f"<div class='over'><div class='oh'><b>Over {o['over']}</b><small>{esc(bowler)} · {o['runs']} run{'s' if o['runs'] != 1 else ''}{wk_txt} · {esc(o['phase'])}</small></div><div class='balls'>{_balls(o['balls'])}</div></div>")
        out.append(f"<div class='card'><h3 style='color:{COL[k % 2]}'>{esc(inn['team'])}</h3>{''.join(rows)}</div>")
    return "".join(out) or _empty("Ball-by-ball data was not recorded for this match.")


def _commentary(rep: dict[str, Any]) -> str:
    rows = []
    for inn in rep["innings"]:
        for b in inn["balls"]:
            if b.get("commentary") and (b.get("wicket") or bat_runs(b) in (4, 6)):
                rows.append((inn["number"], b))
    if not rows:
        return ""
    items = "".join(f"<div class='cm {'w' if b.get('wicket') else 'b'}'><span>{_i(b.get('over')) - 1}.{_i(b.get('ball'))}</span>{esc(str(b['commentary']).replace(chr(10), ' ')[:240])}</div>" for _, b in rows[:24])
    return f"<section class='section' id='comm'><h2>Highlights commentary</h2>{items}</section>"


def render_report(payload: dict[str, Any]) -> str:
    rep = build_report(payload)
    status = _status(payload.get("termination"))
    inns = rep["innings"]
    title = f"Crickium · {payload.get('game_name') or 'Game'} · Match {payload.get('match_number') or ''}"
    narr = "".join(f"<p>{esc(p)}</p>" for p in rep["narrative"])
    ins = "".join(f"<div class='ins'>{esc(x)}</div>" for x in rep["insights"]) or _empty("Not enough ball telemetry for automatic insights.")
    body = [_hero(payload, rep), _nav(rep), _kpis(rep)]
    sec = lambda sid, h, content: f"<section class='section' id='{sid}'><h2>{h}</h2>{content}</section>"
    body.append(sec("report", "Match report", f"<div class='prose'>{narr}</div>"))
    body.append(sec("insights", "Key insights", ins))
    body.append(sec("worm", "Run progression", _worm(rep) + _over_bars(rep) + _rr_compare(rep)))
    body.append(sec("win", "Win probability", _win_chart(rep)))
    body.append(sec("h2h", "Head to head", _h2h(rep)))
    body.append(sec("phases", "Phase analysis", _phases(rep)))
    body.append(sec("cards", "Scorecards", "".join(_scorecard(i, k) for k, i in enumerate(inns)) or _empty("No innings were recorded before the match ended.")))
    body.append(sec("wkts", "Wickets &amp; partnerships", _wickets(rep)))
    body.append(sec("tactics", "Tactics breakdown", _tactics(rep)))
    body.append(sec("moments", "Key moments", _moments(rep)))
    body.append(sec("impact", "Player impact", _impact(rep)))
    body.append(_commentary(rep))
    body.append(sec("bbb", "Ball by ball", _bbb(rep)))
    if payload.get("super_over_rounds"):
        rows = "".join(f"<div class='row'><b>Decider round {k}</b><span>{esc(str(r)[:200])}</span></div>" for k, r in enumerate(payload["super_over_rounds"], 1))
        body.append(sec("so", "Super Over history", rows))
    return (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("{{TITLE}}", esc(title)).replace("{{CSS}}", THEME_PATH.read_text(encoding="utf-8"))
            .replace("{{BODY}}", "".join(body)).replace("{{MATCH_NUMBER}}", num(payload.get("match_number"))))
