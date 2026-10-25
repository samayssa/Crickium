"""Group-message texts for the tournament (pure: rows in, HTML strings out)."""
from __future__ import annotations

import html
from typing import Any, Mapping, Sequence

from services import tournament_core as tc
from services import tournament_text as tx
from utils.mentions import mention_html

BOARD_TITLES = {
    "orange": ("🟠", "Orange Cap — Most Runs"), "purple": ("🟣", "Purple Cap — Most Wickets"), "mvp": ("⭐", "Most Valuable Player"),
    "hundreds": ("💯", "Most Hundreds"), "fifties": ("5️⃣0️⃣", "Most Fifties"), "fastest100": ("⚡", "Fastest Hundred"),
    "fastest50": ("⚡", "Fastest Fifty"), "sixes": ("🚀", "Most Sixes"), "fours": ("🎯", "Most Fours"),
    "highest": ("🏏", "Highest Score"), "bestbowl": ("🎳", "Best Bowling"), "sr": ("📈", "Best Strike Rate"), "economy": ("🧮", "Best Economy"),
}
RECORD_BOARDS = ("hundreds", "fifties", "fastest100", "fastest50", "sixes", "fours", "highest", "bestbowl", "sr", "economy")


def e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def owner_mention(team: Mapping[str, Any] | None) -> str:
    if not team or not team.get("owner_id"):
        return "—"
    return mention_html(int(team["owner_id"]), team.get("owner_username"), team.get("owner_name"))


def _short(team: Mapping[str, Any] | None, width: int = 8) -> str:
    s = tx.team_short(team)
    return s if len(s) <= width else s[: width - 1] + "…"


def table_text(t: Mapping[str, Any], table: Sequence[Mapping[str, Any]], played: int, total: int) -> str:
    qualify = int(tc.playoff_plan(len(table)).get("qualify") or 0)
    rows = [" #  TEAM      P  W  L  PTS    NRR"]
    for r in table:
        mark = "▸" if qualify and int(r["pos"]) <= qualify else " "
        rows.append(f"{mark}{int(r['pos']):<2} {_short(r, 8):<8} {r['played']:>2} {r['won']:>2} {r['lost']:>2} {r['points']:>4} {tc.fmt_nrr(r['nrr']):>7}")
    foot = f"▸ top {qualify} reach the playoffs · " if qualify else ""
    return (f"🏆 <b>{e(t.get('name'))}</b>\n📊 <b>Points Table</b> · {played}/{total} matches\n"
            f"<pre>{e(chr(10).join(rows))}</pre><i>{foot}2 pts a win · ties by wins, then NRR</i>")


def leaders_text(t: Mapping[str, Any], board: str, rows: Sequence[Mapping[str, Any]], teams: Mapping[int, Mapping[str, Any]]) -> str:
    icon, title = BOARD_TITLES.get(board, ("🏅", board))
    head = f"{icon} <b>{e(title)}</b> · {e(t.get('name'))}"
    if not rows:
        return head + "\n<i>Nothing yet — play some matches first.</i>"
    out = []
    for i, r in enumerate(rows, start=1):
        team = _short(teams.get(int(r.get("team_no") or 0)), 6)
        v = r.get("value")
        if board == "orange":
            d = f"<b>{v}</b> runs · {r.get('inns')} inns · HS {r.get('best')}"
        elif board == "purple":
            d = f"<b>{v}</b> wkts · {r.get('runs_given')} runs"
        elif board == "mvp":
            d = f"<b>{v}</b> pts · {r.get('matches')} m"
        elif board in ("fastest50", "fastest100"):
            d = f"<b>{v}</b> balls · {r.get('runs')} runs (M{r.get('fno')})"
        elif board == "highest":
            d = f"<b>{v}</b> ({r.get('balls')})" + ("" if r.get("dismissed") else "*")
        elif board == "bestbowl":
            d = f"<b>{v}/{r.get('runs_given')}</b> (M{r.get('fno')})"
        elif board == "sr":
            d = f"<b>{v}</b> · {r.get('runs')} off {r.get('balls')}"
        elif board == "economy":
            d = f"<b>{v}</b> · {r.get('wickets')} wkts"
        else:
            d = f"<b>{v}</b> · {r.get('runs')} runs"
        out.append(f"{i}. {e(r.get('name'))} <i>({e(team)})</i> — {d}")
    return head + "\n<blockquote>" + "\n".join(out) + "</blockquote>"


def _status_icon(f: Mapping[str, Any]) -> str:
    return {"done": "✅", "live": "🔴", "scheduled": "🕓"}.get(str(f.get("status")), "🕓")


def fixture_line(f: Mapping[str, Any], teams: Mapping[int, Mapping[str, Any]]) -> str:
    h, a = teams.get(int(f["home_team_no"])), teams.get(int(f["away_team_no"]))
    line = f"{_status_icon(f)} M{int(f['fno'])} · <b>{e(_short(h, 6))}</b> vs <b>{e(_short(a, 6))}</b>"
    if f.get("status") == "done" and f.get("result_text"):
        line += f"\n      <i>{e(f['result_text'])}</i>"
    elif f.get("status") == "scheduled" and (f.get("ready_home") or f.get("ready_away")):
        line += " · ⏳ waiting for both"
    return line


def day_card(t: Mapping[str, Any], day: Mapping[str, Any], fixtures: Sequence[Mapping[str, Any]], teams: Mapping[int, Mapping[str, Any]]) -> str:
    window = tc.fmt_window(int(t.get("window_start_min") or tc.WINDOW_START_MIN), int(t.get("window_end_min") or tc.WINDOW_END_MIN))
    lines = [f"📅 <b>Match Day {int(day['day_no'])}</b> · {e(tc.fmt_day(day['play_date']))}\n🏆 {e(t.get('name'))}\n🕔 <b>{e(window)}</b>", ""]
    for f in fixtures:
        h, a = teams.get(int(f["home_team_no"])), teams.get(int(f["away_team_no"]))
        stage = f" [{tc.STAGE_SHORT.get(f.get('stage'), '')}]" if f.get("stage") != "league" else ""
        lines.append(fixture_line(f, teams) + stage)
        lines.append(f"      👤 {owner_mention(h)} vs {owner_mention(a)}")
    lines.append("\n<i>Both players tap “Ready” (or send /tplay) — the match starts as soon as the group is free.</i>")
    return "\n".join(lines)


def reminder_text(t: Mapping[str, Any], f: Mapping[str, Any], home: Mapping[str, Any], away: Mapping[str, Any]) -> str:
    ph, pa = bool(f.get("present_home")), bool(f.get("present_away"))
    return (f"⏰ <b>Your time is going!</b> · {e(t.get('name'))}\n"
            f"M{int(f['fno'])} · <b>{e(_short(home, 8))}</b> vs <b>{e(_short(away, 8))}</b>\n"
            f"{'✅' if ph else '⬜'} {owner_mention(home)}\n{'✅' if pa else '⬜'} {owner_mention(away)}\n"
            f"<blockquote>Your match is still pending. Tap <b>Present</b> now — if only one of you shows up, "
            f"that player gets a walkover; if neither does, the match is abandoned with 0 points.</blockquote>")


def present_markup(tid: int, f: Mapping[str, Any]) -> dict:
    return tx.markup([tx.button("✅ Present (home)", f"trp:{tid}:{int(f['fno'])}:h", style="success"),
                      tx.button("✅ Present (away)", f"trp:{tid}:{int(f['fno'])}:a", style="success")], [tx.hub_button(tid)])


def lobby_text(t: Mapping[str, Any], teams: Sequence[Mapping[str, Any]]) -> str:
    prof = tc.profile(t.get("engine"))
    mode = "🏛 Auction" if t.get("mode") == "auction" else "⚡ Ready squads"
    rows = []
    for i in range(1, int(t.get("max_teams") or 0) + 1):
        team = teams[i - 1] if i <= len(teams) else None
        rows.append(f"{i}. {tx.team_label(team)}" if team else f"{i}. <i>open slot</i>")
    lines = [f"🏆 <b>{e(t.get('name'))}</b>" + (" · 🎖 Official" if t.get("kind") == "official" else ""),
             f"<blockquote>{e((prof.get('icon','') + ' ' + str(prof.get('title',''))).strip())} · {mode}\n🕔 {e(tc.fmt_window(int(t.get('window_start_min') or 1020), int(t.get('window_end_min') or 1260)))}\n"
             f"👥 {len(teams)}/{int(t.get('max_teams') or 0)} teams</blockquote>", "\n".join(rows)]
    if t.get("mode") == "auction":
        lines.append(f"💰 Purse <b>{e(tc.fmt_money(t.get('purse_lakh')))}</b> · 📈 steps: " + ", ".join(f"{e(a)} {e(b)}" for a, b in tc.ladder_rows(t.get("ladder"))[:4]))
    from services import tournament_prize as tp
    if (t.get("prize") or {}).get("state") == "held" and tp.describe(t.get("prize")):
        lines.append(f"🎁 <b>Prize pool:</b> {e(tp.describe(t['prize']))}")
    lines.append("<i>Tap Join to take a team.</i>")
    return "\n".join(lines)


def champion_text(t: Mapping[str, Any], champ: Mapping[str, Any] | None, runner: Mapping[str, Any] | None) -> str:
    return (f"🏆 <b>{e(t.get('name'))} — CHAMPIONS!</b>\n"
            f"🥇 <b>{e(tx.team_label(champ, owner=False))}</b> · {owner_mention(champ)}\n"
            f"🥈 {e(tx.team_label(runner, owner=False))} · {owner_mention(runner)}")
