from __future__ import annotations

print("pxl.py loaded")

import html

from handlers.registry import register
from app import app
from engines.lineup_engine import load_current_xi
from database.user_stats_repo import ensure_franchise_name
from utils.country_flags import flag_for
from utils.debut_gate import validate_playing_xi


def _captain_id(players: list[dict]) -> int | None:
    if not players:
        return None
    player = max(
        players,
        key=lambda p: (
            int(p.get("bat_level") or 0) + int(p.get("bowl_level") or 0),
            int(p.get("player_id") or 0),
        ),
    )
    return int(player.get("player_id") or 0)


def _line(player: dict, number: int, icon: str, captain_id: int | None) -> str:
    name = html.escape(str(player.get("name") or "Player"))
    level = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    flag = flag_for(player.get("country"))
    cap = " 🧢" if captain_id and int(player.get("player_id") or 0) == captain_id else ""
    return f"{number}. {name} • {level} {flag} {icon}{cap}"


def _render_pxl(xi: list[dict], team_name: str) -> str:
    raw_batsmen = [p for p in xi if p.get("role") == "Batsman"]
    keepers = [p for p in raw_batsmen if p.get("is_wicketkeeper")]
    if not keepers and len(raw_batsmen) == 5:
        keepers = [raw_batsmen[-1]]
    keeper_ids = {int(p.get("player_id") or 0) for p in keepers}
    batsmen = [p for p in raw_batsmen if int(p.get("player_id") or 0) not in keeper_ids]
    allrounders = [p for p in xi if p.get("role") == "AllRounder"]
    bowlers = [p for p in xi if p.get("role") == "Bowler"]
    captain_id = _captain_id(xi)

    lines = [
        "╭━━━〔 🏏 PLAYING XI 〕━━━╮",
        "",
        f"➤ <b>{html.escape(team_name)}</b>",
        f"➤ 👥 <b>Players:</b> {len(xi)}/11",
        "",
        "<blockquote>",
        "<b>🏏 Batsmen</b>",
    ]
    for i, player in enumerate(batsmen, start=1):
        lines.append(f"├ {_line(player, i, '🏏', captain_id)}")
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>🧤 Wicket-Keeper</b>"]
    for i, player in enumerate(keepers, start=len(batsmen) + 1):
        prefix = "╰" if i == len(batsmen) + len(keepers) else "├"
        lines.append(f"{prefix} {_line(player, i, '🧤', captain_id)}")
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>🔄 All-Rounders</b>"]
    start = len(batsmen) + len(keepers) + 1
    for idx, player in enumerate(allrounders, start=start):
        prefix = "╰" if idx == len(batsmen) + len(keepers) + len(allrounders) else "├"
        lines.append(f"{prefix} {_line(player, idx, '🔄', captain_id)}")
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>⚡ Bowlers</b>"]
    start = len(batsmen) + len(keepers) + len(allrounders) + 1
    for idx, player in enumerate(bowlers, start=start):
        prefix = "╰" if idx == 11 else "├"
        lines.append(f"{prefix} {_line(player, idx, '⚡', captain_id)}")
    lines += [
        "</blockquote>",
        "",
        "➤ 📋 <b>Full Squad:</b> /squad",
        "",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ]
    return "\n".join(lines)


@register("pxl")
async def pxl_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    first_name = from_user.get("first_name")

    xi = await load_current_xi(user_id) or []
    if len(xi) < 11:
        await app.send_message(
            chat_id,
            "⚠️ Your Playing XI is incomplete. You need exactly 11 players.",
            parse_mode="HTML",
        )
        return

    xi = xi[:11]
    valid, reason = validate_playing_xi(xi)
    if not valid:
        await app.send_message(
            chat_id,
            "<b>⚠️ Your Playing XI is not perfect.</b>\n\n"
            "You need <b>3-4 batsmen, 1-2 wicket-keepers, "
            "3-4 all-rounders and 3-4 bowlers</b> to use this Playing XI.",
            parse_mode="HTML",
        )
        return

    team_name = await ensure_franchise_name(user_id, first_name)
    await app.send_message(chat_id, _render_pxl(xi, team_name), parse_mode="HTML")
