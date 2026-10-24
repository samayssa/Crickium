from __future__ import annotations

import html
import re

from handlers.registry import register
from app import app
from database.query import fetch
from database.playint_teams_repo import TEAM_MAP as INT_TEAMS
from database.playipl_teams_repo import TEAM_MAP as IPL_TEAMS


def _normalise(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip()).strip()
    return raw.upper()


def _resolve(raw: str):
    value = _normalise(raw)
    for prefix, mapping in (("T20I-", INT_TEAMS), ("IPL-", IPL_TEAMS)):
        if value.startswith(prefix):
            candidate = value[len(prefix):]
            for code, name in mapping.items():
                if candidate == code or candidate == _normalise(name):
                    return ("T20I" if mapping is INT_TEAMS else "IPL"), code, name
            return None
    for code, name in INT_TEAMS.items():
        if value in {code, _normalise(name)}:
            return "T20I", code, name
    for code, name in IPL_TEAMS.items():
        aliases = {code, _normalise(name)}
        if code == "RCB":
            aliases.add("ROYAL CHALLENGERS BANGALORE")
        if value in aliases:
            return "IPL", code, name
    return None


@register("team")
async def team_command(message):
    chat_id = int(message["chat"]["id"])
    parts = str(message.get("text") or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await app.send_message(
            chat_id,
            "⚠️ Use <code>/team &lt;team name&gt;</code> or <code>/team &lt;short name&gt;</code>.\n\n"
            "Examples: <code>/team India</code> • <code>/team RCB</code> • <code>/team Royal Challengers Bengaluru</code>",
            parse_mode="HTML",
        )
        return

    resolved = _resolve(parts[1])
    if not resolved:
        await app.send_message(chat_id, "⚠️ I couldn't find that team in the supported game-engine databases.", parse_mode="HTML")
        return

    engine, code, full_name = resolved
    rows = await fetch(
        "SELECT name, country, role, bat_level, bowl_level FROM playint_players WHERE engine_key=$1 AND team_code=$2 ORDER BY player_id ASC;",
        engine, code,
    )
    if not rows:
        await app.send_message(chat_id, f"⚠️ <b>{html.escape(full_name)}</b> is not uploaded in the {engine} database yet.", parse_mode="HTML")
        return

    lines = [f"<b>🏏 {html.escape(full_name)}</b>", f"<blockquote><b>Engine ➤ {html.escape(engine)}</b>", "", "<b>SQUAD</b>"]
    for idx, row in enumerate(rows, 1):
        role = html.escape(str(row["role"] or "Player"))
        lines.append(f"{idx}. <b>{html.escape(str(row['name']))}</b> • {role} • BAT {int(row['bat_level'])} • BOWL {int(row['bowl_level'])}")
    lines.append("</blockquote>")
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
