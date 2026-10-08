from __future__ import annotations

import html
from pathlib import Path
from typing import Any

def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


ROOT = Path(__file__).resolve().parent
THEME_PATH = ROOT / "theme.css"
TEMPLATE_PATH = ROOT / "template.html"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def num(value: Any, digits: int = 0) -> str:
    try:
        x = float(value or 0)
    except Exception:
        x = 0.0
    return f"{x:.{digits}f}" if digits else f"{int(round(x)):,}"


def overs(balls: int) -> str:
    balls = int(balls or 0)
    return f"{balls // 6}.{balls % 6}"


def score_text(snap: dict[str, Any]) -> str:
    return f"{num(snap.get('runs'))}/{num(snap.get('wickets'))}"


def _status_label(value: str) -> str:
    return str(value or "completed").replace("_", " ").title()


def _team_colors(index: int) -> str:
    return "a" if index == 0 else "b"


def _phase_rows(payload: dict[str, Any]) -> str:
    by_key = {}
    for row in payload.get("analytics", {}).get("phase_rows") or []:
        key = (row.get("innings_number"), row.get("team"), row.get("phase"))
        by_key[key] = row
    rows = []
    for row in by_key.values():
        rows.append(
            "<tr>"
            f"<td>{esc(row.get('team'))}</td><td><span class='badge'>{esc(row.get('phase'))}</span></td>"
            f"<td class='num'>{num(row.get('runs'))}</td><td class='num'>{num(row.get('wickets'))}</td>"
            f"<td class='num'>{overs(row.get('legal_balls'))}</td><td class='num'>{num(row.get('rpo'),2)}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan='6' class='muted'>No completed phase data recorded.</td></tr>"


def _bar_chart(payload: dict[str, Any]) -> str:
    innings = payload.get("innings") or []
    over_map = {}
    max_over = 0
    for idx, snap in enumerate(innings[:2]):
        for row in snap.get("analysis_overs") or []:
            over = int(row.get("over") or 0)
            max_over = max(max_over, over)
            over_map.setdefault(over, {})[idx] = int(row.get("runs") or 0)
    if not over_map:
        return "<div class='note'>No over-by-over telemetry was recorded for this match.</div>"
    cap = max(6, max((v for d in over_map.values() for v in d.values()), default=6))
    cols = []
    for over in range(1, max_over + 1):
        d = over_map.get(over, {})
        ha = round((d.get(0, 0) / cap) * 100)
        hb = round((d.get(1, 0) / cap) * 100)
        cols.append(
            f"<div class='bar-col'><span class='bar a' style='height:{ha}%;' title='Innings 1 over {over}: {d.get(0,0)} runs'></span>"
            f"<span class='bar b' style='height:{hb}%;' title='Innings 2 over {over}: {d.get(1,0)} runs'></span>"
            f"<span class='bar-label'>{over}</span></div>"
        )
    return "<div class='bar-chart'>" + "".join(cols) + "</div>"


def _line_svg(series: list[dict[str, Any]], *, value_key: str, y_min: float, y_max: float, team_mode: bool = True) -> str:
    if not series:
        return "<div class='note'>No chart data available.</div>"
    width, height, left, right, top, bottom = 760, 250, 48, 22, 18, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    max_x = max(int(x.get("over") or 1) for x in series)
    def x_pos(over: int) -> float:
        return left + (max_x and (over - 1) / max(1, max_x - 1) or 0) * plot_w
    def y_pos(value: float) -> float:
        ratio = (value - y_min) / max(1e-9, y_max - y_min)
        return top + (1 - ratio) * plot_h
    by_team = {}
    for row in series:
        by_team.setdefault(str(row.get("team") or "Series"), []).append(row)
    paths = []
    colors = ["line-a", "line-b"]
    for idx, (team, rows) in enumerate(list(by_team.items())[:2]):
        pts = " ".join(f"{x_pos(int(r.get('over') or 1)):.1f},{y_pos(float(r.get(value_key) or 0)):.1f}" for r in rows)
        paths.append(f"<polyline class='{colors[idx]}' points='{pts}'><title>{esc(team)}</title></polyline>")
    grid = []
    for i in range(5):
        val = y_min + (y_max - y_min) * i / 4
        y = y_pos(val)
        grid.append(f"<line class='gridline' x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}'/><text class='axis' x='{left-8}' y='{y+4:.1f}' text-anchor='end'>{esc(num(val,1))}</text>")
    return f"<svg class='chart' viewBox='0 0 {width} {height}' role='img'>{''.join(grid)}{''.join(paths)}</svg>"


def _prob_svg(series: list[dict[str, Any]]) -> str:
    return _single_svg(series, key="probability", y_min=0, y_max=100, line_class="line-b", title="Chasing-side report model win probability")


def _single_svg(series: list[dict[str, Any]], *, key: str, y_min: float, y_max: float, line_class: str, title: str) -> str:
    if not series:
        return "<div class='note'>No chart data available.</div>"
    width, height, left, right, top, bottom = 760, 250, 48, 22, 18, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    max_x = max(int(x.get("over") or 1) for x in series)
    def xp(over: int) -> float:
        return left + ((over - 1) / max(1, max_x - 1)) * plot_w if max_x > 1 else left + plot_w / 2
    def yp(v: float) -> float:
        return top + (1 - ((v - y_min) / max(1e-9, y_max-y_min))) * plot_h
    pts = " ".join(f"{xp(int(r.get('over') or 1)):.1f},{yp(float(r.get(key) or 0)):.1f}" for r in series)
    grid=[]
    for i in range(5):
        value = y_min + (y_max-y_min)*i/4
        y=yp(value)
        grid.append(f"<line class='gridline' x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}'/><text class='axis' x='{left-8}' y='{y+4:.1f}' text-anchor='end'>{esc(num(value,1) if y_max <= 10 else num(value))}</text>")
    return f"<svg class='chart' viewBox='0 0 {width} {height}' role='img' aria-label='{esc(title)}'>{''.join(grid)}<polyline class='{line_class}' points='{pts}'/></svg>"


def _momentum_svg(payload: dict[str, Any]) -> str:
    series = payload.get("analytics", {}).get("momentum_series") or []
    pressure = payload.get("analytics", {}).get("pressure_series") or []
    if not series and not pressure:
        return "<div class='note'>Momentum telemetry is not available.</div>"
    width, height, left, right, top, bottom = 760, 250, 48, 22, 18, 28
    plot_w, plot_h = width-left-right, height-top-bottom
    max_x=max([int(x.get('over') or 1) for x in series+pressure] or [1])
    def xp(o): return left + ((o-1)/max(1,max_x-1))*plot_w if max_x>1 else left+plot_w/2
    def yp(v): return top + (1-(v+2)/4)*plot_h
    lines=[]
    for cls, rows in (("line-a",series),("line-c",pressure)):
        if rows:
            pts=" ".join(f"{xp(int(r.get('over') or 1)):.1f},{yp(float(r.get('value') or 0)):.1f}" for r in rows)
            lines.append(f"<polyline class='{cls}' points='{pts}'/>")
    grid=[]
    for value in (-2,-1,0,1,2):
        y=yp(value); grid.append(f"<line class='gridline' x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}'/><text class='axis' x='{left-8}' y='{y+4:.1f}' text-anchor='end'>{value:+.0f}</text>")
    return f"<svg class='chart' viewBox='0 0 {width} {height}' role='img' aria-label='Momentum and pressure'>{''.join(grid)}{''.join(lines)}</svg>"


def _scorecard(snap: dict[str, Any]) -> str:
    bat_rows=[]
    for b in snap.get("batters") or []:
        runs=int(b.get("runs") or 0); balls=int(b.get("balls") or 0); sr=runs*100/balls if balls else 0
        status="NOT OUT" if not b.get("dismissed") else (b.get("dismissal_text") or "OUT")
        bat_rows.append(f"<tr><td>{esc(b.get('name'))}</td><td class='muted'>{esc(status)}</td><td class='num'>{runs}</td><td class='num'>{balls}</td><td class='num'>{int(b.get('fours') or 0)}</td><td class='num'>{int(b.get('sixes') or 0)}</td><td class='num'>{sr:.1f}</td></tr>")
    bowl_rows=[]
    for b in snap.get("bowlers") or []:
        balls=int(b.get("balls") or 0); runs=int(b.get("runs") or 0); wk=int(b.get("wickets") or 0); econ=runs*6/balls if balls else 0
        maidens=int(b.get('maidens') or 0); bowl_rows.append(f"<tr><td>{esc(b.get('name'))}</td><td class='num'>{overs(balls)}</td><td class='num'>{maidens}</td><td class='num'>{runs}</td><td class='num'>{wk}</td><td class='num'>{econ:.2f}</td></tr>")
    extras = sum(int(v or 0) for v in (snap.get("extras") or {}).values())
    return (
        f"<div class='scroll'><table class='report-table scorecard-bat'><thead><tr><th>Batter</th><th>Status</th><th class='num'>R</th><th class='num'>B</th><th class='num'>4s</th><th class='num'>6s</th><th class='num'>SR</th></tr></thead><tbody>"
        + ("".join(bat_rows) or "<tr><td colspan='7' class='muted'>No batter telemetry.</td></tr>")
        + "</tbody></table></div>"
        f"<div class='scroll' style='margin-top:10px'><table class='report-table scorecard-bowl'><thead><tr><th>Bowler</th><th class='num'>O</th><th class='num'>M</th><th class='num'>R</th><th class='num'>W</th><th class='num'>Econ</th></tr></thead><tbody>"
        + ("".join(bowl_rows) or "<tr><td colspan='6' class='muted'>No bowler telemetry.</td></tr>")
        + f"</tbody></table></div><div class='note'>Team extras: {extras} • Fall-of-wicket events recorded: {len(snap.get('wickets_fallen') or [])}</div>"
    )


def _balls(value: list[dict[str, Any]]) -> str:
    out=[]
    for b in value:
        if b.get("wicket"): cls="w"; label="W"
        elif b.get("outcome") == "wide": cls="x"; label="WD"
        elif b.get("outcome") == "no_ball": cls="x"; label="NB"
        elif int(b.get("runs") or 0)==6: cls="six"; label="6"
        elif int(b.get("runs") or 0)==4: cls="four"; label="4"
        elif int(b.get("runs") or 0)==0: cls="x"; label="•"
        else: cls="x"; label=str(int(b.get("runs") or 0))
        out.append(f"<span class='ball {cls}' title='{esc((b.get('batter') or 'Player') + ' • ' + (b.get('outcome') or ''))}'>{esc(label)}</span>")
    return "<div class='ball-row'>"+"".join(out)+"</div>"


def _duel(payload: dict[str, Any]) -> str:
    sections=[]
    for snap in payload.get("innings") or []:
        overs=snap.get("analysis_overs") or []
        if not overs: continue
        rows=[]
        for row in overs:
            rows.append(
                f"<tr><td class='num'>{num(row.get('over'))}</td><td>{esc(row.get('batting_approach'))}</td><td>{esc(row.get('bowling_plan'))}</td>"
                f"<td class='num'>{num(row.get('runs'))}</td><td class='num'>{num(row.get('wickets'))}</td><td>{_balls(row.get('balls') or [])}</td></tr>"
            )
        sections.append(
            f"<h3>{esc(snap.get('batting_team_display'))} • decision trace</h3>"
            "<div class='scroll'><table class='report-table duel-table'><thead><tr><th class='num'>Over</th><th>Batting approach</th><th>Bowling plan</th><th class='num'>R</th><th class='num'>W</th><th>Ball path</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>"
        )
    return "".join(sections) or "<div class='note'>No tactical telemetry was recorded.</div>"


def _heatmap(payload: dict[str, Any]) -> str:
    cells = payload.get("analytics", {}).get("strategy_matrix") or []
    if not cells: return "<div class='note'>No approach-pair data available.</div>"
    bats=sorted({str(x.get('batting') or 'Balanced') for x in cells})
    bowls=sorted({str(x.get('bowling') or 'Mixed') for x in cells})
    lookup={(str(x.get('batting')),str(x.get('bowling'))):x for x in cells}
    vals=[float(x.get('rpo') or 0) for x in cells]
    low, high=min(vals), max(vals)
    def level(v):
        if high<=low: return 3
        return max(1,min(5,1+int((v-low)/(high-low)*4)))
    html_out=f"<div class='heatmap' style='--cols:{len(bowls)}'><div class='heat'><strong>Bat ↓ / Bowl →</strong></div>"
    html_out += "".join(f"<div class='heat'><strong>{esc(x)}</strong></div>" for x in bowls)
    for bat in bats:
        html_out+=f"<div class='heat'><strong>{esc(bat)}</strong></div>"
        for bowl in bowls:
            x=lookup.get((bat,bowl))
            if not x: html_out+="<div class='heat'>•</div>"
            else: html_out+=f"<div class='heat level{level(float(x.get('rpo') or 0))}'><strong>{num(x.get('rpo'),1)}</strong><small>{num(x.get('runs'))}r • {num(x.get('wickets'))}w • {num(x.get('overs'))}ov</small></div>"
    return html_out+"</div>"


def _fall_of_wickets(payload: dict[str, Any]) -> str:
    rows=[]
    for snap in payload.get("innings") or []:
        team= snap.get("batting_team_display") or "TEAM"
        for idx, w in enumerate(snap.get("wickets_fallen") or [], start=1):
            rows.append(f"<tr><td>{esc(team)}</td><td class='num'>{idx}</td><td>{esc(w.get('batter') or 'Player')}</td><td>{esc(w.get('type') or 'Wicket')}</td><td>{esc(w.get('fielder') or '—')}</td><td>{esc(w.get('over') or '—')}</td></tr>")
    return "".join(rows) or "<tr><td colspan='6' class='muted'>No recorded fall-of-wicket events.</td></tr>"


def _extras_table(payload: dict[str, Any]) -> str:
    rows=[]
    for snap in payload.get("innings") or []:
        extras=snap.get("extras") or {}
        total=sum(_safe_int(v) for v in extras.values())
        rows.append(f"<tr><td>{esc(snap.get('batting_team_display') or 'TEAM')}</td><td class='num'>{total}</td><td class='num'>{_safe_int(extras.get('wides') or extras.get('wide'))}</td><td class='num'>{_safe_int(extras.get('no_balls') or extras.get('no_ball'))}</td><td class='num'>{_safe_int(extras.get('byes') or 0)}</td><td class='num'>{_safe_int(extras.get('leg_byes') or extras.get('legbye') or 0)}</td></tr>")
    return "".join(rows) or "<tr><td colspan='6' class='muted'>No extras were recorded.</td></tr>"


def _partnership_table(payload: dict[str, Any]) -> str:
    rows=[]
    for p in payload.get("analytics", {}).get("partnerships") or []:
        rows.append(f"<tr><td>{esc(p.get('team'))}</td><td>{num(p.get('wicket'))}</td><td>{esc(p.get('partnership'))}</td><td class='num'>{num(p.get('runs'))}</td><td class='num'>{num(p.get('balls'))}</td><td>{esc(p.get('status'))}</td></tr>")
    return "".join(rows) or "<tr><td colspan='6' class='muted'>No partnership sequence recorded.</td></tr>"


def _impact_table(payload: dict[str, Any]) -> str:
    rows=[]
    for p in payload.get("analytics", {}).get("player_impact") or []:
        stat=(f"{num(p.get('runs'))} ({num(p.get('balls'))})" if p.get('type')=='Bat' else f"{num(p.get('wickets'))}W • {num(p.get('economy'),2)}")
        rows.append(f"<tr><td>{esc(p.get('name'))}</td><td>{esc(p.get('team'))}</td><td>{esc(p.get('type'))}</td><td class='num'>{stat}</td><td class='num'>{num(p.get('impact'),1)}</td></tr>")
    return "".join(rows) or "<tr><td colspan='5' class='muted'>No impact metrics available.</td></tr>"


def _turning_points(payload: dict[str, Any]) -> str:
    pts=payload.get("analytics",{}).get("turning_points") or []
    if not pts: return "<div class='note'>No large statistical swing crossed the report threshold.</div>"
    return "<ol class='list'>"+"".join(f"<li><b>{esc(p.get('kind'))} • Over {num(p.get('over'))}</b>: {num(p.get('from'),1)} → {num(p.get('to'),1)} ({'+' if float(p.get('change') or 0)>=0 else ''}{num(p.get('change'),1)})</li>" for p in pts)+"</ol>"


def _conditions(payload: dict[str, Any]) -> str:
    m=payload
    metrics=m.get("analytics",{}).get("metrics",{})
    best=m.get("analytics",{}).get("metrics",{}).get("best_over") or {}
    low=m.get("analytics",{}).get("metrics",{}).get("lowest_over") or {}
    statements=[
        f"Pitch profile: <b>{esc(m.get('pitch') or 'Standard')}</b>. No physical pitch change is fabricated; late-phase scoring is shown as an observed proxy.",
        f"Venue: <b>{esc(m.get('stadium') or 'Virtual ground')}</b>. Weather field: <b>{esc(m.get('weather') or 'Not recorded')}</b>.",
        f"Report-par rate baseline: <b>{num(metrics.get('par_rpo'),2)} RPO</b>. Boundary runs contributed <b>{num(metrics.get('boundary_run_pct'),1)}%</b> of recorded runs.",
        f"Dot-ball share: <b>{num(metrics.get('dot_ball_pct'),1)}%</b>. Strike-rotation proxy: <b>{num(metrics.get('strike_rotation_pct'),1)}%</b> of legal balls.",
        f"Best scoring over: <b>{num(best.get('over'))}</b> for <b>{num(best.get('runs'))}</b>. Lowest: <b>{num(low.get('over'))}</b> for <b>{num(low.get('runs'))}</b>.",
    ]
    return "<ul class='list'>"+"".join(f"<li>{x}</li>" for x in statements)+"</ul>"


def render_report(payload: dict[str, Any]) -> str:
    innings=payload.get("innings") or []
    analytics=payload.get("analytics") or {}
    players=payload.get("players") or []
    team_a=innings[0] if innings else {}
    team_b=innings[1] if len(innings)>1 else {}
    title=f"Crickium • {payload.get('game_name') or 'Game'} • Match {payload.get('match_number') or ''}"
    winner=payload.get("winner_name") or "No winner"
    result=(f"{esc(winner)} won" if payload.get("winner_id") else ("Match exited / ended early" if payload.get("termination")!='completed' else "Match tied or undecided"))
    status=_status_label(payload.get("termination"))
    metrics=analytics.get("metrics") or {}
    header=(
        "<section class='hero'>"
        f"<div class='eyebrow'>CRICKIUM MATCH INTELLIGENCE • {esc(payload.get('game_name') or 'GAME')}</div>"
        f"<h1>{esc(team_a.get('batting_team_display') or (players[0].get('first_name') if players else 'Player 1'))} <span class='muted'>vs</span> {esc(team_b.get('batting_team_display') or (players[1].get('first_name') if len(players)>1 else 'Player 2'))}</h1>"
        f"<p class='hero-sub'>{esc(status)} • Report #{num(payload.get('match_number'))} • {esc(payload.get('format') or 'T20')}</p>"
        "<div class='hero-grid'>"
        f"<div class='team-box'><span class='team-label'>Innings 1</span><div class='team-name'>{esc(team_a.get('batting_team_display') or 'Team A')}</div><div class='score'>{score_text(team_a)}</div><div class='overs'>{esc(team_a.get('over_text') or '0.0')} overs</div></div>"
        f"<div class='vs'>VS</div>"
        f"<div class='team-box'><span class='team-label'>Innings 2</span><div class='team-name'>{esc(team_b.get('batting_team_display') or 'Pending')}</div><div class='score'>{score_text(team_b) if team_b else '—'}</div><div class='overs'>{esc(team_b.get('over_text') or '0.0') if team_b else '—'} overs</div></div>"
        "</div>"
        f"<div class='result-strip'>🏆 {result} • Termination: {esc(status)}{(' • ' + esc(payload.get('reason'))) if payload.get('reason') else ''}</div>"
        "<div class='meta-grid'>"
        f"<div class='meta'><span class='k'>Match ID</span><span class='v'>#{num(payload.get('match_id'))}</span></div>"
        f"<div class='meta'><span class='k'>Ground</span><span class='v'>{esc(payload.get('stadium'))}</span></div>"
        f"<div class='meta'><span class='k'>Pitch</span><span class='v'>{esc(payload.get('pitch'))}</span></div>"
        f"<div class='meta'><span class='k'>Weather</span><span class='v'>{esc(payload.get('weather'))}</span></div>"
        f"<div class='meta'><span class='k'>Toss</span><span class='v'>{esc(payload.get('toss_winner_id') or 'Not recorded')}</span></div>"
        f"<div class='meta'><span class='k'>Decision</span><span class='v'>{esc(payload.get('decision') or 'Not recorded')}</span></div>"
        f"<div class='meta'><span class='k'>Player of Match</span><span class='v'>{esc((payload.get('potm') or {}).get('name') or '—')}</span></div>"
        f"<div class='meta'><span class='k'>Report status</span><span class='v'>{esc(status)}</span></div>"
        "</div></section>"
    )
    body=[header]
    body.append(
        "<section class='section'><h2>Match pulse</h2><div class='kpis'>"
        f"<div class='kpi'><span class='label'>Par rate</span><span class='value'>{num(metrics.get('par_rpo'),2)}</span></div>"
        f"<div class='kpi'><span class='label'>Dot balls</span><span class='value'>{num(metrics.get('dot_ball_pct'),1)}%</span></div>"
        f"<div class='kpi'><span class='label'>Boundary runs</span><span class='value'>{num(metrics.get('boundary_run_pct'),1)}%</span></div>"
        f"<div class='kpi'><span class='label'>Rotation proxy</span><span class='value'>{num(metrics.get('strike_rotation_pct'),1)}%</span></div>"
        f"<div class='kpi'><span class='label'>Max boundary streak</span><span class='value'>{num(metrics.get('max_boundary_streak'))}</span></div>"
        "</div></section>"
    )
    body.append("<section class='section'><h2>Phase readout</h2><div class='scroll'><table class='report-table phase-table'><thead><tr><th>Team</th><th>Phase</th><th class='num'>Runs</th><th class='num'>Wickets</th><th class='num'>Overs</th><th class='num'>Rate</th></tr></thead><tbody>"+_phase_rows(payload)+"</tbody></table></div></section>")
    body.append("<section class='section'><h2>Runs by over</h2>"+_bar_chart(payload)+"<div class='legend'><span><i class='dot a'></i>Innings 1</span><span><i class='dot b'></i>Innings 2</span></div><p class='note'>Bars are the recorded over totals. No simulation values are recreated or changed for the report.</p></section>")
    body.append("<section class='section'><h2>The worm</h2>"+_line_svg(analytics.get('innings_series') or [], value_key='cumulative_runs', y_min=0, y_max=max([int(x.get('cumulative_runs') or 0) for x in analytics.get('innings_series') or []] or [10]))+"<div class='legend'><span><i class='dot a'></i>Innings 1</span><span><i class='dot b'></i>Innings 2</span></div><p class='note'>Cumulative score path by over, retained from recorded match events.</p></section>")
    body.append("<section class='section'><h2>Momentum &amp; pressure</h2>"+_momentum_svg(payload)+"<div class='legend'><span><i class='dot a'></i>Momentum</span><span><i class='dot c'></i>Chase pressure</span></div><p class='note'>Momentum is a report-derived ±2 indicator. Pressure rises as the chase model becomes more demanding.</p></section>")
    body.append("<section class='section'><h2>Win probability model</h2>"+_prob_svg(analytics.get('win_series') or [])+"<div class='highlight'>This is an analytical estimate generated from the recorded score, wickets, balls remaining and required run rate. It is not the engine's decision probability.</div></section>")
    body.append("<section class='section'><h2>Scorecards</h2>"+"".join(f"<h3>{esc(s.get('batting_team_display'))} — {score_text(s)} ({esc(s.get('over_text') or '0.0')})</h3>"+_scorecard(s) for s in innings[:2])+"</section>")
    body.append("<section class='section'><h2>Turning points</h2>"+_turning_points(payload)+"</section>")
    body.append("<section class='section'><h2>Partnerships &amp; wickets</h2><h3>Partnership sequence</h3><div class='scroll'><table class='report-table partnership-table'><thead><tr><th>Team</th><th class='num'>Wkt</th><th>Pair</th><th class='num'>Runs</th><th class='num'>Balls</th><th>State</th></tr></thead><tbody>"+_partnership_table(payload)+"</tbody></table></div><h3>Fall of wickets</h3><div class='scroll'><table class='report-table wicket-table'><thead><tr><th>Team</th><th class='num'>Wkt</th><th>Batter</th><th>Event</th><th>Fielder</th><th class='num'>Over</th></tr></thead><tbody>"+_fall_of_wickets(payload)+"</tbody></table></div><h3>Extras ledger</h3><div class='scroll'><table class='report-table extras-table'><thead><tr><th>Team</th><th class='num'>Total</th><th class='num'>Wides</th><th class='num'>No-balls</th><th class='num'>Byes</th><th class='num'>Leg-byes</th></tr></thead><tbody>"+_extras_table(payload)+"</tbody></table></div><p class='note'>When a live engine does not expose a detailed dismissal/fielder label, the report preserves the recorded event and shows an em dash rather than fabricating data.</p></section>")
    streaks=analytics.get('strategy_streaks') or {}
    combos=analytics.get('strategy_combinations') or []
    combo_html="".join("<div class='pair'><div class='name'>"+esc(x.get('batting'))+" × "+esc(x.get('bowling'))+"</div><div class='score-mini'>"+num(x.get('runs'))+"r • "+num(x.get('wickets'))+"w • "+num(x.get('rpo'),2)+" RPO</div></div>" for x in combos[:8]) or "<div class='note'>No paired strategy events recorded.</div>"
    body.append("<section class='section'><h2>The approach duel</h2>"+_duel(payload)+"<h3>Approach pairing map</h3>"+_heatmap(payload)+"<div class='duo' style='margin-top:12px'>"+"".join("<div class='card'><h3>"+esc(x.get('group'))+" • "+esc(x.get('label'))+"</h3><div class='muted small'>Overs: "+num(x.get('overs'))+" • Runs: "+num(x.get('runs'))+" • Wickets: "+num(x.get('wickets'))+"</div><div style='margin-top:4px;font-weight:900'>"+num(x.get('rpo'),2)+" RPO</div></div>" for x in (analytics.get('strategy_summary') or [])[:8])+"</div><div class='duo' style='margin-top:12px'><div class='card'><h3>Longest intent streak</h3><div class='highlight'>"+esc(streaks.get('batting',{}).get('label') or '—')+" × "+num(streaks.get('batting',{}).get('length'))+" overs</div></div><div class='card'><h3>Longest bowling-plan streak</h3><div class='highlight'>"+esc(streaks.get('bowling',{}).get('label') or '—')+" × "+num(streaks.get('bowling',{}).get('length'))+" overs</div></div></div><h3>Tactical combinations</h3>"+combo_html+"</section>")
    body.append("<section class='section'><h2>Player impact board</h2><div class='scroll'><table class='report-table impact-table'><thead><tr><th>Player</th><th>Team</th><th>Discipline</th><th class='num'>Primary output</th><th class='num'>Impact index</th></tr></thead><tbody>"+_impact_table(payload)+"</tbody></table><p class='note'>Impact index is a report-only composite used to rank observed contribution across batting and bowling.</p></div></section>")
    body.append("<section class='section'><h2>How the pitch changed</h2><div class='highlight'>Pitch profile: <b>"+esc(payload.get('pitch') or 'Standard')+"</b>. The report does not fabricate physical wear. It surfaces observed scoring-rate shifts as the match progressed.</div><div class='duo' style='margin-top:12px'><div class='card'><h3>Opening phase</h3><div class='score-mini'>"+num((analytics.get('phase_map') or {}).get((1,'Powerplay'),{}).get('rpo'),2)+" RPO</div></div><div class='card'><h3>Late phase</h3><div class='score-mini'>"+num((analytics.get('phase_map') or {}).get((2,'Death'),{}).get('rpo'),2)+" RPO</div></div></div></section>")
    body.append("<section class='section'><h2>What the conditions did</h2>"+_conditions(payload)+"</section>")
    if payload.get("super_over_rounds"):
        rows=[]
        for i, round_data in enumerate(payload.get("super_over_rounds") or [], start=1):
            rows.append(f"<div class='pair'><div class='name'>Decider round {i}</div><div class='score-mini'>{esc(str(round_data)[:180])}</div></div>")
        body.append("<section class='section'><h2>Super Over history</h2>"+"".join(rows)+"</section>")
    return TEMPLATE_PATH.read_text(encoding="utf-8").replace("{{TITLE}}", esc(title)).replace("{{CSS}}", THEME_PATH.read_text(encoding="utf-8")).replace("{{BODY}}", "".join(body)).replace("{{MATCH_NUMBER}}", num(payload.get("match_number")))
