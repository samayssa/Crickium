"""Post-match report for Mini App games started from a group (/playai).

* summary_card()   - the standard 1024x576 Crickium match-summary card
                     (services.match_summary, same template + coordinates)
* analysis_html()  - a self-contained, offline HTML analysis of the match
* deliver()        - posts both into the group as a reply to the command
"""

from __future__ import annotations

import html
import json
import time
from typing import Any

import services.miniapp_game_view as gv
from services import match_summary as ms

E = html.escape


def _legal(tok: str) -> bool:
    return tok not in ("wd", "nb")


# ---------------------------------------------------------------------------
# summary card
# ---------------------------------------------------------------------------
def _snapshots(view: dict) -> list[dict]:
    snaps = []
    for sc in view.get("scorecards", []):
        snaps.append({
            "innings_number": sc["idx"] + 1,
            "batting_team_display": sc["team"],
            "bowling_team_display": sc["bowling_team"],
            "runs": sc["runs"], "wickets": sc["wkts"], "over_text": sc["overs"],
            "batters": [{"name": b["name"], "runs": b["runs"], "balls": b["balls"]}
                        for b in sc["batters"] if b["state"] != "yet"],
            "bowlers": [{"name": b["name"], "runs": b["runs"], "balls": b["balls"], "wickets": b["wkts"]}
                        for b in sc["bowlers"]],
        })
    return snaps


def _potm_figures(snaps: list[dict], name: str) -> dict:
    """Batting and bowling line of the player of the match (best innings of each)."""
    out = {"name": name or "—", "runs": 0, "balls": 0}
    for sn in snaps:
        for b in sn["batters"]:
            if b["name"] == name and b["runs"] >= out["runs"]:
                out["runs"], out["balls"] = b["runs"], b["balls"]
        for b in sn["bowlers"]:
            if b["name"] == name and b["balls"] and (b["wickets"], -b["runs"]) > (out.get("wickets", -1), -out.get("bowl_runs", 10 ** 6)):
                out["wickets"], out["bowl_runs"] = b["wickets"], b["runs"]
    return out


def summary_card(game: dict, match_number: int = 0) -> bytes:
    view = gv.build_view(game)
    snaps = _snapshots(view)
    res = view.get("result") or {}
    pom = res.get("pom") or {}
    potm = _potm_figures(snaps, pom.get("name") or "")
    text = res.get("text") or "Match complete"
    return ms.render_match_summary(snaps, winner=text, margin="", potm=potm, match_number=match_number, result_text=text)


# ---------------------------------------------------------------------------
# analysis HTML
# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#07110f;--card:#0f1c17;--card2:#14261f;--line:#243a30;--text:#f2f5ed;--mut:#95a69b;--lime:#c5ef61;--gold:#efc66b;--ai:#ff9f6e;--red:#ff6f66;--blue:#6aa8ff;--pur:#c58bff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:16px 14px 60px}
h1,h2,h3{margin:0}h2{font-size:13px;letter-spacing:.16em;color:var(--lime);text-transform:uppercase;margin:26px 2px 10px}
.hero{padding:20px;border-radius:24px;border:1px solid #3b5232;background:linear-gradient(135deg,#25401d,#12201a 62%,#0d1612)}
.hero small{letter-spacing:.2em;color:var(--lime);font-weight:800;font-size:11px}.hero h1{font-size:26px;margin:6px 0}
.hero p{margin:4px 0;color:#c0cfc4}.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{padding:4px 10px;border-radius:99px;background:rgba(255,255,255,.08);font-size:12px;color:#d3e0d7}
.result{margin-top:14px;padding:14px;border-radius:16px;background:rgba(0,0,0,.3);text-align:center}
.result b{display:block;font-size:19px;color:var(--gold)}.result span{color:var(--mut);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;text-align:center}
.tile b{display:block;font-size:20px;color:var(--lime)}.tile small{color:var(--mut);font-size:11px;letter-spacing:.08em}
.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px;margin-bottom:12px}
.panel h3{font-size:16px;margin-bottom:8px;display:flex;justify-content:space-between;gap:10px}.panel h3 span{color:var(--gold);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th{font-size:10.5px;letter-spacing:.1em;color:var(--mut);text-align:right;padding:6px 4px}
th:first-child,td:first-child{text-align:left}td{text-align:right;padding:7px 4px;border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
td small{display:block;color:var(--mut);font-size:11.5px}.tw{overflow-x:auto}.tot td{font-weight:800;color:var(--lime)}
svg{width:100%;height:auto;display:block}.leg{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:#c0cfc4;margin-top:6px}
.leg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
.wpbar{display:flex;height:14px;border-radius:99px;overflow:hidden;background:#1b2c25;margin:8px 0 4px}.wpbar .u{background:linear-gradient(90deg,#9fc838,#c5ef61)}.wpbar .a{background:linear-gradient(90deg,#ff8a5c,#ff9f6e)}
.row{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:13.5px}.row span{color:var(--mut)}
.ball{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:50%;margin:2px;font-size:12px;font-weight:800;background:#22332b;padding:0 4px}
.b4{background:#2f6fd6}.b6{background:#8a3fd1}.bW{background:#d6453d}.bx{background:#b9892e;color:#1a1205}.b0{background:#1a2822;color:var(--mut)}.b1{background:#2c5c2e}
.over{padding:8px 0;border-top:1px solid var(--line)}.over small{color:var(--mut)}.over b{font-size:13px}
.ins{padding:10px 12px;border-left:3px solid var(--lime);background:rgba(197,239,97,.06);border-radius:0 12px 12px 0;margin:8px 0;font-size:14px}
.bar{height:8px;border-radius:9px;background:#1b2c25;overflow:hidden}.bar i{display:block;height:100%;background:#9fc838}
footer{margin-top:30px;text-align:center;color:var(--mut);font-size:12px}
@media print{body{background:#fff;color:#000}}
"""


def _ball_chip(tok: str) -> str:
    cls = {"0": "b0", "4": "b4", "6": "b6", "W": "bW", "wd": "bx", "nb": "bx"}.get(tok, "b1" if tok in ("1", "2", "3") else "b0")
    return f'<span class="ball {cls}">{E("•" if tok == "0" else tok)}</span>'


def _line_chart(series: list[list[float]], width=640, height=190, color="#c5ef61", ymax=100.0, mid=True) -> str:
    if len(series) < 2:
        return '<div style="color:#95a69b">Not enough data.</div>'
    xs = [p[0] for p in series]
    x0, x1 = min(xs), max(xs) or 1
    pad = 28
    X = lambda x: pad + (x - x0) / max(1e-9, (x1 - x0)) * (width - pad - 8)
    Y = lambda y: height - 22 - (y / ymax) * (height - 36)
    pts = " ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in series)
    area = f"{X(series[0][0]):.1f},{Y(0):.1f} " + pts + f" {X(series[-1][0]):.1f},{Y(0):.1f}"
    grid = "".join(f'<line x1="{pad}" x2="{width-8}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#243a30" stroke-dasharray="{"4 4" if v!=50 else "0"}"/>'
                   f'<text x="2" y="{Y(v)+4:.1f}" fill="#95a69b" font-size="10">{int(v)}</text>' for v in (0, 25, 50, 75, 100))
    return (f'<svg viewBox="0 0 {width} {height}" role="img">{grid}<polygon points="{area}" fill="{color}" opacity=".10"/>'
            f'<polyline fill="none" stroke="{color}" stroke-width="2.6" stroke-linejoin="round" points="{pts}"/></svg>')


def _worm(view: dict, game: dict) -> str:
    width, height, pad = 640, 220, 30
    inns = view["stats"]["innings"]
    ymax = max(10, max((p[1] for i in inns for p in i["worm"]), default=10))
    xmax = max(1, view["cfg"]["overs"])
    X = lambda x: pad + x / xmax * (width - pad - 10)
    Y = lambda y: height - 24 - y / ymax * (height - 40)
    cols = ["#c5ef61", "#ff9f6e"]
    out = [f'<svg viewBox="0 0 {width} {height}">']
    for v in range(0, 5):
        yy = ymax * v / 4
        out.append(f'<line x1="{pad}" x2="{width-10}" y1="{Y(yy):.1f}" y2="{Y(yy):.1f}" stroke="#243a30" stroke-dasharray="4 4"/><text x="2" y="{Y(yy)+4:.1f}" fill="#95a69b" font-size="10">{int(yy)}</text>')
    for o in range(0, xmax + 1, max(1, xmax // 10)):
        out.append(f'<text x="{X(o):.1f}" y="{height-6}" fill="#95a69b" font-size="10" text-anchor="middle">{o}</text>')
    for k, inn in enumerate(inns):
        pts = " ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in inn["worm"])
        out.append(f'<polyline fill="none" stroke="{cols[k % 2]}" stroke-width="2.6" stroke-linejoin="round" points="{pts}"/>')
        real = game["inn"][k]
        # wicket markers at the cumulative score when each wicket fell
        for f in real["fow"]:
            ov = float(str(f["ov"]).split(".")[0]) + float(str(f["ov"]).split(".")[1]) / 6.0 if "." in str(f["ov"]) else float(f["ov"])
            out.append(f'<circle cx="{X(ov):.1f}" cy="{Y(f["runs"]):.1f}" r="4.2" fill="#d6453d" stroke="#07110f" stroke-width="1.5"/>')
    out.append("</svg>")
    leg = "".join(f'<span><i style="background:{cols[k % 2]}"></i>{E(i["team"])}</span>' for k, i in enumerate(inns))
    return "".join(out) + f'<div class="leg">{leg}<span><i style="background:#d6453d"></i>Wicket</span></div>'


def _over_bars(inn_view: dict, color: str, overs: int) -> str:
    rows = inn_view["overs_log"]
    if not rows:
        return ""
    width, height, pad = 640, 150, 26
    mx = max(8, max(r["runs"] for r in rows))
    bw = (width - pad - 8) / max(len(rows), overs if overs <= 20 else len(rows))
    out = [f'<svg viewBox="0 0 {width} {height}">']
    for i, r in enumerate(rows):
        h = r["runs"] / mx * (height - 44)
        x = pad + i * bw + 2
        fill = "#d6453d" if r["wkts"] else color
        out.append(f'<rect x="{x:.1f}" y="{height-24-h:.1f}" width="{max(4, bw-4):.1f}" height="{h:.1f}" rx="3" fill="{fill}" opacity="{.95 if not r["wkts"] else 1}"/>'
                   f'<text x="{x+(bw-4)/2:.1f}" y="{height-26-h:.1f}" fill="#f2f5ed" font-size="10" text-anchor="middle">{r["runs"]}</text>'
                   f'<text x="{x+(bw-4)/2:.1f}" y="{height-8}" fill="#95a69b" font-size="10" text-anchor="middle">{r["no"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _scorecard_html(sc: dict) -> str:
    bat = "".join(
        f'<tr><td><b>{E(b["name"])}</b><small>{E(b["how"] or "not out") if b["state"] == "out" else "not out"}</small></td>'
        f'<td><b>{b["runs"]}</b></td><td>{b["balls"]}</td><td>{b["fours"]}</td><td>{b["sixes"]}</td><td>{b["dots"]}</td><td>{b["sr"]}</td></tr>'
        for b in sc["batters"] if b["state"] != "yet")
    bowl = "".join(
        f'<tr><td><b>{E(b["name"])}</b><small>{E(b["bw_label"] or "")}</small></td><td>{b["overs"]}</td><td>{b["maidens"]}</td><td>{b["runs"]}</td>'
        f'<td><b>{b["wkts"]}</b></td><td>{b["dots"]}</td><td>{b["wd"]}/{b["nb"]}</td><td>{b["econ"]}</td></tr>' for b in sc["bowlers"])
    fow = "".join(f'<span class="chip">{f["runs"]}-{f["w"]} {E(f["name"])} ({f["ov"]})</span> ' for f in sc["fow"])
    ps = "".join(f'<div class="row"><b>{p["runs"]} ({p["balls"]})</b><span>{E(p["a"])} &amp; {E(p["b"])}</span></div>' for p in sc["partnerships"])
    dnb = f'<p style="color:#95a69b;font-size:13px"><b>Did not bat:</b> {", ".join(E(n) for n in sc["did_not_bat"])}</p>' if sc["did_not_bat"] else ""
    return f"""<div class="panel"><h3>{E(sc["team"])}<span>{sc["runs"]}/{sc["wkts"]} ({sc["overs"]} ov)</span></h3>
<div class="tw"><table><tr><th>BATTER</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>DOT</th><th>SR</th></tr>{bat}
<tr><td><b>Extras</b><small>{E(sc["extras_text"] or "none")}</small></td><td colspan="6">{sc["extras"]}</td></tr>
<tr class="tot"><td>TOTAL</td><td colspan="6">{sc["runs"]}/{sc["wkts"]} · {sc["overs"]} ov · RR {sc["rr"]}</td></tr></table></div>{dnb}
<h3 style="margin-top:14px">Bowling · {E(sc["bowling_team"])}</h3><div class="tw"><table><tr><th>BOWLER</th><th>O</th><th>M</th><th>R</th><th>W</th><th>DOT</th><th>WD/NB</th><th>ECO</th></tr>{bowl}</table></div>
{f'<h3 style="margin-top:14px">Fall of wickets</h3><p>{fow}</p>' if fow else ""}{f'<h3 style="margin-top:14px">Partnerships</h3>{ps}' if ps else ""}</div>"""


def _bbb(game: dict, k: int) -> str:
    inn = game["inn"][k]
    bat_xi, bowl_xi = game["teams"][inn["bat"]]["xi"], game["teams"][inn["bowl"]]["xi"]
    overs: dict[int, list] = {}
    for lab, tok, runs, bs, ws, wk, ov in inn["log"]:
        overs.setdefault(int(ov), []).append((tok, runs, bs, ws, wk))
    out = []
    for no in sorted(overs):
        balls = overs[no]
        runs = sum(b[1] for b in balls)
        wk = sum(b[4] for b in balls)
        bowler = bowl_xi[balls[0][3]]["name"] if balls and balls[0][3] is not None else "-"
        chips = "".join(_ball_chip(b[0]) for b in balls)
        out.append(f'<div class="over"><b>Over {no}</b> <small>· {E(bowler)} · {runs} runs{f", {wk} wkt" if wk else ""}</small><div>{chips}</div></div>')
    return "".join(out)


def _insights(view: dict, game: dict) -> list[str]:
    ins: list[str] = []
    st = view["stats"]
    user_inn = next((i for i in st["innings"] if i["bat"] == "user"), None)
    ai_inn = next((i for i in st["innings"] if i["bat"] == "ai"), None)
    for lbl, rows in (("batting approach", st["choices"]["bat"]), ("bowling tactic", st["choices"]["bowl"])):
        used = [r for r in rows if r["balls"] >= 6]
        if not used:
            continue
        if lbl == "batting approach":
            best = max(used, key=lambda r: r["sr"])
            ins.append(f'Best {lbl}: <b>{E(best["label"])}</b> — {best["runs"]} runs off {best["balls"]} balls (SR {best["sr"]}).')
            risky = max(used, key=lambda r: r["wkts"] / r["balls"])
            if risky["wkts"]:
                ins.append(f'Costliest {lbl}: <b>{E(risky["label"])}</b> — {risky["wkts"]} wicket(s) in {risky["balls"]} balls.')
        else:
            best = min(used, key=lambda r: r["rpb"] - 2.0 * r["wkts"] / r["balls"])
            ins.append(f'Most effective {lbl}: <b>{E(best["label"])}</b> — {best["runs"]} runs, {best["wkts"]} wkts from {best["balls"]} balls.')
    if user_inn and ai_inn:
        d = round(user_inn["dot_pct"] - ai_inn["dot_pct"], 1)
        ins.append(f'Dot-ball pressure: you faced {user_inn["dot_pct"]}% dots vs the computer\'s {ai_inn["dot_pct"]}%' + (" — you rotated the strike better." if d < 0 else " — rotating the strike earlier could have helped."))
        ins.append(f'Boundary share: you hit boundaries off {user_inn["boundary_pct"]}% of balls; the computer {ai_inn["boundary_pct"]}%.')
    for inn in st["innings"]:
        if inn["phases"]:
            ph = max(inn["phases"], key=lambda p: p["rr"])
            ins.append(f'{E(inn["team"])} scored fastest in the <b>{E(ph["label"])}</b> ({ph["rr"]} rpo).')
    series = (view["wp"].get("series") or [])
    if len(series) > 3:
        swing = max(range(1, len(series)), key=lambda i: abs(series[i][1] - series[i - 1][1]))
        d = series[swing][1] - series[swing - 1][1]
        ins.append(f'Biggest win-probability swing: <b>{abs(d):.1f}%</b> {"towards" if d > 0 else "against"} you around delivery {int(series[swing][0])}.')
    return ins


def analysis_html(game: dict, match_number: int = 0, when: str | None = None) -> str:
    v = gv.build_view(game)
    cfg, t, res = v["cfg"], v["teams"], v.get("result") or {}
    scs, st = v["scorecards"], v["stats"]
    pom = res.get("pom") or {}
    when = when or time.strftime("%d %b %Y, %H:%M UTC", time.gmtime())
    won = res.get("winner") == "user"
    tiles = []
    for inn in st["innings"]:
        tiles.append(f'<div class="tile"><b>{inn["runs"]}/{inn["wkts"]}</b><small>{E(inn["team"]).upper()}</small></div>')
    tiles += [f'<div class="tile"><b>{v["balls_played"]}</b><small>DELIVERIES</small></div>']
    cmp_rows = "".join(f'<tr><td style="text-align:right;{"color:var(--lime);font-weight:800" if r["lead"]=="user" else ""}">{E(r["user"])}</td><td style="text-align:center;color:var(--mut)">{E(r["label"])}</td><td style="text-align:left;{"color:var(--ai);font-weight:800" if r["lead"]=="ai" else ""}">{E(r["ai"])}</td></tr>' for r in st["compare"])
    phases = ""
    for inn in st["innings"]:
        ph = "".join(f'<div class="row"><b>{E(p["label"])}</b><span>{p["runs"]}/{p["wkts"]} in {p["balls"]} balls · {p["rr"]} rpo</span></div>' for p in inn["phases"])
        extra = ""
        if inn["best_partnership"]:
            bp = inn["best_partnership"]
            extra += f'<div class="row"><b>Best partnership</b><span>{bp["runs"]} ({bp["balls"]}) · {E(bp["a"])} &amp; {E(bp["b"])}</span></div>'
        phases += f'<div class="panel"><h3>{E(inn["team"])}<span>RR {inn["rr"]}</span></h3><div class="grid"><div class="tile"><b>{inn["boundary_pct"]}%</b><small>BOUNDARY BALLS</small></div><div class="tile"><b>{inn["dot_pct"]}%</b><small>DOT BALLS</small></div><div class="tile"><b>{inn["fours"]}/{inn["sixes"]}</b><small>FOURS / SIXES</small></div><div class="tile"><b>{inn["extras"]}</b><small>EXTRAS</small></div></div>{ph}{extra}</div>'
    def choices(rows, title, bat):
        if not rows:
            return ""
        body = "".join(
            f'<tr><td><b>{r["emoji"]} {E(r["label"])}</b></td><td>{r["balls"]}</td><td>{r["runs"]}</td><td>{r["wkts"]}</td><td>{r["boundaries"]}</td><td>{r["sr"] if bat else r["rpb"]}</td></tr>' for r in rows)
        return f'<div class="panel"><h3>{title}</h3><div class="tw"><table><tr><th>CHOICE</th><th>BALLS</th><th>RUNS</th><th>WKTS</th><th>BOUND.</th><th>{"SR" if bat else "RUNS/BALL"}</th></tr>{body}</table></div></div>'
    ins = "".join(f'<div class="ins">{x}</div>' for x in _insights(v, game))
    lineups = ""
    for side in ("user", "ai"):
        rows = "".join(f'<tr><td>{p["pos"]}. {p["role_icon"]} <b>{E(p["name"])}</b><small>{E(p["role_label"])}{" · " + E(p["bw_label"]) if p["bw_label"] else ""}</small></td><td>{p["bat"]}</td><td>{p["bowl"]}</td></tr>' for p in (v.get("lineups") or {}).get(side, []))
        lineups += f'<div class="panel"><h3>{E(t[side]["name"])}<span>{"YOU" if side == "user" else "COMPUTER"}</span></h3><div class="tw"><table><tr><th>PLAYER</th><th>BAT</th><th>BOWL</th></tr>{rows}</table></div></div>'
    wp = v["wp"]
    wp_ser = [[p[0], p[1]] for p in (wp.get("series") or [])]
    sc_html = "".join(_scorecard_html(sc) for sc in scs)
    over_html = "".join(f'<div class="panel"><h3>{E(sc["team"])} · runs per over</h3>{_over_bars(sc, "#c5ef61" if k == 0 else "#ff9f6e", cfg["overs"])}</div>' for k, sc in enumerate(scs))
    bbb_html = "".join(f'<div class="panel"><h3>{E(sc["team"])} · ball by ball</h3>{_bbb(game, k)}</div>' for k, sc in enumerate(scs))
    toss = E(v["toss"]["text"]) if v.get("toss") else "—"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crickium AI Match Analysis</title><style>{CSS}</style></head><body><div class="wrap">
<div class="hero"><small>CRICKIUM · MATCH ANALYSIS{f" · GAME #{match_number}" if match_number else ""}</small>
<h1>{E(t["user"]["name"])} vs {E(t["ai"]["name"])}</h1><p>{E(when)}</p>
<div class="chips"><span class="chip">{E(cfg["difficulty_label"])} difficulty</span><span class="chip">{cfg["overs"]} overs</span><span class="chip">{cfg["pitch_emoji"]} {E(cfg["pitch_label"])} pitch</span><span class="chip">{"Ball by ball" if cfg["sim"] == "ball" else "Over by over"}</span></div>
<div class="result"><b>{E(res.get("text") or "Match complete")}</b><span>{"🏆 Victory" if won else ("🤝 Tied" if res.get("type") == "tie" else "Defeat")} · Toss: {toss}</span></div></div>
<h2>Match at a glance</h2><div class="grid">{"".join(tiles)}</div>
{f'<div class="panel" style="margin-top:12px"><h3>Player of the match<span>{E(pom.get("name") or "")}</span></h3><div style="color:var(--mut)">{E(pom.get("line") or "")}</div></div>' if pom else ""}
<h2>Key insights</h2>{ins or '<div class="ins">Not enough deliveries for insights.</div>'}
<h2>Win probability</h2><div class="panel"><div class="row" style="border:0;padding:0"><b style="color:var(--lime)">{E(t["user"]["name"])} {wp["user"]}%</b><b style="color:var(--ai)">{wp["ai"]}% {E(t["ai"]["name"])}</b></div>
<div class="wpbar"><span class="u" style="width:{wp["user"]}%"></span><span class="a" style="width:{wp["ai"]}%"></span></div>
{_line_chart(wp_ser)}<div class="leg"><span>Line = {E(t["user"]["name"])} win chance over deliveries</span></div></div>
<h2>Run progression</h2><div class="panel">{_worm(v, game)}</div>{over_html}
<h2>Head to head</h2><div class="panel"><div class="tw"><table><tr><th style="text-align:right">{E(st["innings"][0]["team"] if st["innings"] and st["innings"][0]["bat"] == "user" else t["user"]["name"])}</th><th style="text-align:center">&nbsp;</th><th style="text-align:left">{E(t["ai"]["name"])}</th></tr>{cmp_rows}</table></div></div>
<h2>Phase &amp; innings analysis</h2>{phases}
<h2>Your decisions</h2>{choices(st["choices"]["bat"], "Batting approaches", True)}{choices(st["choices"]["bowl"], "Bowling tactics", False)}
<h2>Scorecards</h2>{sc_html}
<h2>Ball by ball</h2>{bbb_html}
<h2>Squads</h2>{lineups}
<footer>Generated by Crickium · every figure comes from the match record</footer></div></body></html>"""


# ---------------------------------------------------------------------------
# delivery to the group
# ---------------------------------------------------------------------------
def _caption(game: dict, link: dict) -> str:
    res = game.get("result") or {}
    uid = int(link["user_id"])
    name = E(str(link.get("user_name") or game["teams"]["user"]["name"] or "Player"))
    won = res.get("winner") == "user"
    head = "🏆 <b>VICTORY VS AI</b>" if won else ("🤝 <b>MATCH TIED VS AI</b>" if res.get("type") == "tie" else "🏏 <b>MATCH COMPLETE VS AI</b>")
    return (f'<blockquote>{head}\n\n<a href="tg://user?id={uid}">{name}</a> <b>vs</b> {E(game["teams"]["ai"]["name"])}\n'
            f'🎯 <b>{E(res.get("text") or "")}</b>\n'
            f'⚙️ {E(game["cfg"]["difficulty"].title())} · {game["cfg"]["overs"]} overs\n'
            f'📊 Full analysis report attached below.</blockquote>')


async def deliver(game: dict, link: dict, bot_token: str, match_number: int = 0) -> bool:
    import httpx
    chat_id = int(link["chat_id"])
    reply = link.get("message_id")
    base = f"https://api.telegram.org/bot{bot_token}"
    card = summary_card(game, match_number)
    doc = analysis_html(game, match_number).encode("utf-8")
    fname = f"Crickium_AI_Match_{match_number or int(time.time())}.html"
    ok = True
    async with httpx.AsyncClient(timeout=60) as client:
        async def send(method: str, data: dict, files: dict) -> bool:
            body = dict(data)
            if reply:
                body["reply_parameters"] = json.dumps({"message_id": int(reply), "allow_sending_without_reply": True})
            r = await client.post(f"{base}/{method}", data=body, files=files)
            if r.status_code >= 400 and "reply_parameters" in body:
                body.pop("reply_parameters")
                files = {k: (v[0], v[1], v[2]) for k, v in files.items()}
                r = await client.post(f"{base}/{method}", data=body, files=files)
            if r.status_code >= 400:
                print(f"[miniapp_game_report] {method} failed {r.status_code}: {r.text[:200]}")
            return r.status_code < 400
        ok &= await send("sendPhoto", {"chat_id": chat_id, "caption": _caption(game, link), "parse_mode": "HTML"},
                         {"photo": ("crickium_summary.png", card, "image/png")})
        ok &= await send("sendDocument", {"chat_id": chat_id, "caption": "🧠 <b>Match analysis</b> — open in any browser (works offline).", "parse_mode": "HTML"},
                         {"document": (fname, doc, "text/html")})
    return ok
