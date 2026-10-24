"""Tournament-only "MATCH READY" card, written as a Telegram Rich Message (Bot API 10.1+: real headings,
dividers and lists via ``sendRichMessage``). Matches outside a tournament keep their old card; if Telegram
rejects the rich message the caller falls back to that old card too, so a match can never get stuck here.
"""
from __future__ import annotations

import html
import traceback
from typing import Any

from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_engines as te
from services import tournament_text as tx

_FORMAT = {"PLAYIPL": "IPL T20 · 20 overs a side · Impact Player", "PLAYWPL": "WPL T20 · 20 overs a side · Impact Player",
           "PLAYINT": "T20 International · 20 overs a side", "PLAY": "Crickium T20 · 20 overs a side"}
_TYPE = {"ipl": "IPL franchise tournament", "wpl": "WPL franchise tournament", "t20wc": "T20 World Cup", "custom": "Custom tournament"}


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def build_html(t: dict, fx: dict, home: dict, away: dict, match: dict, total_fixtures: int) -> str:
    from engines.play_engine import pitch_label
    from utils.mentions import display_name
    engine = str(t.get("engine") or "PLAY").upper()
    stage = str(fx.get("stage") or "league")
    stage_txt = f"{tc.STAGE_LABELS.get(stage, stage)} · Match {int(fx['fno'])}" + (f" of {total_fixtures}" if stage == "league" and total_fixtures else "")

    def side(team: dict) -> str:
        emoji = te.team_emoji(engine, team.get("code"))
        short = tx.team_short(team)
        name = str(team.get("name") or "")
        return f"{emoji} {_e(short)}" + (f" — {_e(name)}" if name and name.lower() != short.lower() else "")

    ch, op = int(match["challenger_id"]), int(match["opponent_id"])
    owner = {int(home.get("owner_id") or 0): home, int(away.get("owner_id") or 0): away}
    toss_id = int(match.get("toss_winner_id") or 0)
    toss_name = display_name(match.get("challenger_username"), match.get("challenger_name")) if toss_id == ch else display_name(match.get("opponent_username"), match.get("opponent_name"))
    bat = "BAT" if str(match.get("decision") or "").lower() == "bat" else "BOWL"
    p1 = display_name(match.get("challenger_username"), match.get("challenger_name"))
    p2 = display_name(match.get("opponent_username"), match.get("opponent_name"))
    t1 = owner.get(ch, home) if ch in owner else home
    t2 = owner.get(op, away) if op in owner else away
    rules = [
        f"Win = {tc.POINTS_WIN} points · tie → Super Over · no result = {tc.POINTS_NO_RESULT} point each" if stage == "league" else "Knockout — the winner goes through; a tie is settled by a Super Over",
        "3-minute clock on every decision — miss it and the match is lost",
        "Run-rate (NRR) from the first 20 overs counts for the table",
    ]
    if engine in ("PLAYIPL", "PLAYWPL"):
        rules.append("Impact Player allowed · overseas limit applies")
    return (
        f"<h1>🏆 {_e(t.get('name'))}</h1>"
        f"<h3>{_e(_TYPE.get(str(t.get('preset')), 'Tournament'))} · {_e(stage_txt)}</h3>"
        "<hr/>"
        f"<h2>{side(t1)}</h2>"
        f"<h3>🆚</h3>"
        f"<h2>{side(t2)}</h2>"
        f"<ul><li>👤 {_e(p1)} — {_e(tx.team_short(t1))}</li><li>👤 {_e(p2)} — {_e(tx.team_short(t2))}</li></ul>"
        "<hr/>"
        "<h3>📋 Match details</h3>"
        f"<ul><li>🏆 Format: {_e(_FORMAT.get(engine, 'T20'))}</li>"
        f"<li>🏟 Pitch: {_e(pitch_label(str(match.get('pitch') or 'green')))}</li>"
        f"<li>📍 Stadium: {_e(match.get('stadium'))}</li>"
        f"<li>🌡 Weather: {_e(match.get('weather'))}</li>"
        f"<li>🪙 Toss: {_e(toss_name)} won and chose to {bat}</li></ul>"
        "<h3>📜 Rules</h3>"
        "<ul>" + "".join(f"<li>{_e(r)}</li>" for r in rules) + "</ul>"
        "<blockquote>⚡ The field is set. The first ball awaits…</blockquote>"
    )


async def send(engine: str, match: dict, reply_markup: dict | None) -> dict | None:
    """The tournament MATCH READY card, or None when this match is not a tournament match / the card cannot be sent."""
    try:
        fx = await tr.fixture_by_match(engine, int(match["match_id"]))
        if fx is None:
            return None
        t = await tr.get(int(fx["tid"]))
        home = await tr.team(int(fx["tid"]), int(fx["home_team_no"]))
        away = await tr.team(int(fx["tid"]), int(fx["away_team_no"]))
        if t is None or home is None or away is None:
            return None
        total = len([f for f in await tr.fixtures(int(fx["tid"])) if f.get("stage") == "league"])
        buttons = [list(r) for r in (reply_markup or {}).get("inline_keyboard", [])] + [[tx.hub_button(int(fx["tid"]))]]
        from services.giveaway import tg_call
        sent = await tg_call("sendRichMessage", {
            "chat_id": int(match.get("chat_id") or t["host_chat_id"]),
            "rich_message": {"html": build_html(t, fx, home, away, match, total)},
            "reply_markup": {"inline_keyboard": buttons},
        })
        return sent if isinstance(sent, dict) and sent.get("message_id") else None
    except Exception as exc:
        print(f"[tournament/ready] rich card failed, using the classic card: {exc!r}")
        traceback.print_exc()
        return None
