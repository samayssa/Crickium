"""After the auction: one heading-styled "squad card" per team in the group, tagging the owner, with every player's buying price.

Sent as a Telegram Rich Message (real headings / lists, Bot API 10.1+); if Telegram refuses it the same card goes out as a
normal HTML message, so a team's squad is never lost.
"""
from __future__ import annotations

import asyncio
import html
import traceback
from typing import Any

from database import tournament_auction_repo as ar
from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_text as tx
from services import tournament_tg as _tg
from utils.mentions import mention_html

GAP_SECONDS = 3.2        # a group tolerates ~20 messages a minute


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=False)


def _team_title(team: dict) -> str:
    name = str(team.get("name") or tx.team_short(team))
    short = tx.team_short(team)
    return name if name.lower() == short.lower() else f"{name} ({short})"


def build_html(t: dict, team: dict, squad: list[dict], rank: int | None = None) -> str:
    """Rich-message HTML of one team's squad."""
    xi = [p for p in squad if p.get("xi")]
    bench = [p for p in squad if not p.get("xi")]
    row = lambda p: (f"<li>{tx.role_icon(p.get('role'))} <b>{_e(p.get('name'))}</b> — {_e(tx.role_short(p.get('role')))}"
                     f"{' ✈️' if p.get('overseas') else ''} · <b>{_e(tx.money(p.get('price_lakh')))}</b></li>")
    owner = mention_html(int(team["owner_id"]), team.get("owner_username"), team.get("owner_name")) if team.get("owner_id") else "—"
    spent = sum(int(p.get("price_lakh") or 0) for p in squad)
    out = [f"<h1>🏆 {_e(t.get('name'))}</h1>", "<h3>Auction squads</h3>", "<hr/>",
           f"<h2>{_e(_team_title(team))}</h2>",
           f"<h3>👤 {owner}</h3>",
           f"<p>💰 Spent <b>{_e(tx.money(spent))}</b> · Purse left <b>{_e(tx.money(team.get('purse_left')))}</b> · 👥 {len(squad)} players"
           f" · ✈️ {sum(1 for p in squad if p.get('overseas'))} overseas</p>"]
    if xi:
        out += ["<h3>⭐ Playing XI</h3>", "<ul>" + "".join(row(p) for p in xi) + "</ul>"]
    if bench:
        out += ["<h3>🪑 Bench</h3>", "<ul>" + "".join(row(p) for p in bench) + "</ul>"]
    if not squad:
        out.append("<p><i>No players.</i></p>")
    return "".join(out)


def build_plain(t: dict, team: dict, squad: list[dict]) -> str:
    """Same card for chats where the rich message is refused."""
    owner = mention_html(int(team["owner_id"]), team.get("owner_username"), team.get("owner_name")) if team.get("owner_id") else "—"
    lines = [f"🏆 <b>{_e(t.get('name'))}</b> · Auction squads", f"<b>{_e(_team_title(team))}</b> · 👤 {owner}",
             f"💰 Purse left <b>{_e(tx.money(team.get('purse_left')))}</b> · 👥 {len(squad)} players", ""]
    for p in squad:
        lines.append(f"{'⭐' if p.get('xi') else '▫️'} {tx.role_icon(p.get('role'))} {_e(p.get('name'))} — <b>{_e(tx.money(p.get('price_lakh')))}</b>")
    return "\n".join(lines)


async def send_all(tid: int, delay: float = 5.0) -> int:
    """Post every team's squad card, one after the other. Returns how many were sent."""
    sent = 0
    try:
        await asyncio.sleep(delay)
        t = await tr.get(tid)
        if t is None or not t.get("host_chat_id"):
            return 0
        squads = await ar.all_squads(tid)
        markup = tx.markup([tx.hub_button(tid)])
        for team in sorted(await tr.teams(tid), key=lambda x: int(x["team_no"])):
            squad = squads.get(int(team["team_no"]), [])
            chat = int(t["host_chat_id"])
            try:
                ok = False
                adapter = _tg.get()
                if hasattr(adapter, "send_rich"):
                    ok = bool(await adapter.send_rich(chat, build_html(t, team, squad), markup))
                if not ok:
                    await adapter.send(chat, build_plain(t, team, squad), markup)
                sent += 1
            except Exception as exc:
                print(f"[tournament/squads] team {team.get('team_no')} of #{tid} failed: {exc!r}")
            await asyncio.sleep(GAP_SECONDS)
    except Exception:
        traceback.print_exc()
    return sent
