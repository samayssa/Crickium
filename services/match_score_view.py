import html
import math
import re

TOTAL_BALLS = 120


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


_EMOJI_TAG = re.compile(r"(<tg-emoji\s+emoji-id=\"\d+\">.*?</tg-emoji>)", re.S)


def label_html(value) -> str:
    parts = _EMOJI_TAG.split(str(value if value is not None else ""))
    return "".join(part if _EMOJI_TAG.fullmatch(part) else html.escape(part) for part in parts)


def overs_text(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}"


def strike_rate(runs: int, balls: int) -> str:
    return f"{runs * 100 / balls:.1f}" if balls else "0.0"


def economy(runs: int, balls: int) -> str:
    return f"{runs * 6 / balls:.2f}" if balls else "0.00"


def win_bar(first_pct: float, width: int = 10) -> str:
    filled = max(0, min(width, round(first_pct / 100 * width)))
    return "🟩" * filled + "🟥" * (width - filled)


def projected(runs: int, balls_played: int, rate: float) -> int:
    balls_left = max(0, TOTAL_BALLS - balls_played)
    return int(round(runs + rate * balls_left / 6))


def chase_probability(first_runs: int, runs: int, wickets: int, balls: int) -> float:
    target = max(1, first_runs + 1)
    if runs >= target:
        return 99.0
    balls_left = max(0, TOTAL_BALLS - balls)
    if balls_left <= 0:
        return 1.0
    rrr = max(0, target - runs) * 6.0 / balls_left
    base = max(6.5, min(13.5, first_runs / 20))
    rate_term = 1.0 / (1.0 + math.exp((rrr - base) / 1.15))
    wicket_term = max(0.45, min(1.08, 0.55 + 0.05 * max(0, 10 - wickets)))
    progress_term = 0.80 + 0.20 * (balls / TOTAL_BALLS)
    return max(1.0, min(99.0, rate_term * wicket_term * progress_term * 100.0))


class Blocks:
    def __init__(self):
        self.items = []

    def h1(self, text):
        self.items.append(("h1", text))

    def h2(self, text):
        self.items.append(("h2", text))

    def p(self, text):
        self.items.append(("p", text))

    def hr(self):
        self.items.append(("hr", ""))

    def details(self, summary, lines):
        self.items.append(("details", summary, lines))

    def footer(self, text):
        self.items.append(("footer", text))

    def rich(self) -> str:
        out = []
        for item in self.items:
            kind = item[0]
            if kind == "hr":
                out.append("<hr/>")
            elif kind == "details":
                body = "".join(f"<p>{line}</p>" for line in item[2])
                out.append(f"<details><summary>{item[1]}</summary>{body}</details>")
            else:
                out.append(f"<{kind}>{item[1]}</{kind}>")
        return "\n".join(out)

    def plain(self) -> str:
        out = []
        for item in self.items:
            kind = item[0]
            if kind in ("h1", "h2"):
                out.append(f"\n<b>{item[1]}</b>")
            elif kind == "hr":
                out.append("")
            elif kind == "details":
                out.append(f"<blockquote><b>{item[1]}</b>\n" + "\n".join(item[2]) + "</blockquote>")
            elif kind == "footer":
                out.append(f"\n<i>{item[1]}</i>")
            else:
                out.append(item[1])
        return "\n".join(out).strip()


def _batter_line(slot, striker: bool) -> str:
    if slot is None:
        return "—"
    runs, balls = int(slot.runs or 0), int(slot.balls or 0)
    mark = "🏏" if striker else "▫️"
    return (
        f"{mark} <b>{esc(slot.name)}</b> {runs} ({balls}) · SR {strike_rate(runs, balls)} · "
        f"{int(slot.fours or 0)}×4 · {int(slot.sixes or 0)}×6"
    )


def _bowler_line(name: str, stats: dict) -> str:
    balls = int(stats.get("balls") or 0)
    runs = int(stats.get("runs") or 0)
    return f"🥎 <b>{esc(name)}</b> {int(stats.get('wickets') or 0)}/{runs} ({overs_text(balls)} ov) · Econ {economy(runs, balls)}"


def build_session_view(session, engine_label: str, paused: bool = False) -> Blocks:
    innings = session.innings
    score = innings.score
    runs, wickets, balls = int(score.runs or 0), int(score.wickets or 0), int(score.legal_balls or 0)
    crr = runs * 6 / balls if balls else 0.0
    balls_left = max(0, TOTAL_BALLS - balls)
    first = session.innings_history[0] if session.innings_history else None
    chasing = innings.target is not None and first is not None
    batting = label_html(session.batting_team_display)
    bowling = label_html(session.bowling_team_display)
    view = Blocks()
    view.h1("🏏 LIVE SCORE")
    status = "⏸ Paused" if paused else ("2nd Innings" if chasing else "1st Innings")
    view.p(f"{esc(engine_label)} · {status} · {esc(str(session.pitch).title())} pitch")
    if chasing:
        view.p(f"📋 {label_html(first['batting_team_display'])} <b>{int(first['runs'])}/{int(first['wickets'])}</b> ({esc(first['over_text'])}) · 🎯 Target <b>{int(innings.target)}</b>")
    view.h2(f"{batting} {runs}/{wickets} ({overs_text(balls)})")
    if chasing:
        need = max(0, int(innings.target) - runs)
        rrr = need * 6 / balls_left if balls_left else 0.0
        view.p(f"CRR <b>{crr:.2f}</b> · RRR <b>{rrr:.2f}</b>")
        view.p(f"Need <b>{need}</b> runs in <b>{balls_left}</b> balls")
    else:
        view.p(f"CRR <b>{crr:.2f}</b> · Projected <b>{projected(runs, balls, crr)}</b>")
    view.p(_batter_line(innings.striker, True))
    view.p(_batter_line(innings.non_striker, False))
    view.p(f"🤝 Partnership <b>{int(session.partnership_runs or 0)}</b> ({int(session.partnership_balls or 0)})")
    current = session.current_bowler
    view.h2(f"🎯 {bowling}")
    if current:
        pid = int(current.get("player_id") or 0)
        view.p(_bowler_line(current.get("name") or "Bowler", session.bowler_stats.get(pid, {})))
    others = [
        _bowler_line(data.get("name") or "Bowler", data)
        for pid, data in session.bowler_stats.items()
        if not current or int(pid) != int(current.get("player_id") or 0)
    ]
    if others:
        view.details("Other bowlers", others)
    if chasing:
        pct = chase_probability(int(first["runs"]), runs, wickets, balls)
        view.h2("🎲 Win Probability")
        view.p(f"{batting} <b>{pct:.0f}%</b>")
        view.p(f"{win_bar(pct)}")
        view.p(f"{bowling} <b>{100 - pct:.0f}%</b>")
    return view


def build_so_view(match: dict, state: dict) -> Blocks:
    runs, wickets, balls = int(state.get("runs") or 0), int(state.get("wickets") or 0), int(state.get("legal_balls") or 0)
    target = state.get("target")
    names = {int(match["challenger_id"]): match.get("challenger_name"), int(match["opponent_id"]): match.get("opponent_name")}
    batting = names.get(int(state.get("batting_user") or 0), "Batting side")
    bowling = names.get(int(state.get("bowling_user") or 0), "Bowling side")
    view = Blocks()
    view.h1("⚡ SUPER OVER SCORE")
    view.h2(f"{esc(batting)}  vs  {esc(bowling)}")
    view.p(f"<b>{runs}/{wickets}</b> ({overs_text(balls)} / 1.0 ov)")
    if target is not None:
        need = max(0, int(target) - runs)
        view.p(f"🎯 Target <b>{int(target)}</b> · Need <b>{need}</b> from <b>{max(0, 6 - balls)}</b> balls")
    else:
        view.p(f"⚡ Balls left: <b>{max(0, 6 - balls)}</b>")
    batter_stats = state.get("batter_stats") or {}
    lines = []
    for key in (state.get("current_striker_id"), state.get("current_non_striker_id")):
        data = batter_stats.get(str(key)) if key else None
        if data:
            lines.append(f"🏏 <b>{esc(data.get('name'))}</b> {int(data.get('runs') or 0)} ({int(data.get('balls') or 0)})")
    if lines:
        view.h2("🧢 At The Crease")
        for line in lines:
            view.p(line)
    bowler_stats = state.get("bowler_stats") or {}
    bowler = bowler_stats.get(str(state.get("selected_bowler")))
    if bowler:
        view.h2("🎯 Bowling")
        view.p(_bowler_line(bowler.get("name") or "Bowler", bowler))
        return view
