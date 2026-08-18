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


def _team_status(xi: list[dict]) -> tuple[bool, str]:
    raw_batsmen = [p for p in xi if p.get("role") == "Batsman"]
    keepers = [p for p in xi if str(p.get("role") or "") == "Wicketkeeper"]
    keepers.extend(p for p in raw_batsmen if p.get("is_wicketkeeper"))
    if not keepers and len(raw_batsmen) == 5:
        keepers = [raw_batsmen[-1]]
    keeper_ids = {int(p.get("player_id") or 0) for p in keepers}
    batsmen = [p for p in raw_batsmen if int(p.get("player_id") or 0) not in keeper_ids]
    allrounders = [p for p in xi if p.get("role") == "AllRounder"]
    bowlers = [p for p in xi if p.get("role") == "Bowler"]

    reasons = []
    if len(xi) < 11:
        reasons.append(f"need {11 - len(xi)} more player{'s' if 11 - len(xi) != 1 else ''}")
    elif len(xi) > 11:
        reasons.append(f"remove {len(xi) - 11} player{'s' if len(xi) - 11 != 1 else ''}")

    checks = [
        ("batsman", len(batsmen), 3, 4),
        ("wicket-keeper", len(keepers), 1, 2),
        ("all-rounder", len(allrounders), 3, 4),
        ("bowler", len(bowlers), 3, 4),
    ]
    for label, count, minimum, maximum in checks:
        if count < minimum:
            missing = minimum - count
            reasons.append(f"need {missing} more {label}{'s' if missing != 1 else ''}")
        elif count > maximum:
            extra = count - maximum
            reasons.append(f"remove {extra} {label}{'s' if extra != 1 else ''}")

    if not reasons:
        return True, "Valid"
    return False, "Not Valid — " + ", ".join(reasons)


def _render_pxl(xi: list[dict], team_name: str) -> str:
    raw_batsmen = [p for p in xi if p.get("role") == "Batsman"]
    keepers = [p for p in xi if str(p.get("role") or "") == "Wicketkeeper"]
    keepers.extend(p for p in raw_batsmen if p.get("is_wicketkeeper"))
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
    xi = xi[:11]
    valid, _ = validate_playing_xi(xi)
    _, status_text = _team_status(xi)

    team_name = await ensure_franchise_name(user_id, first_name)
    report = _render_pxl(xi, team_name)
    full_squad_marker = "➤ 📋 <b>Full Squad:</b> /squad"
    report = report.replace(
        full_squad_marker,
        f"➤ 📊 <b>Team Status:</b> {html.escape(status_text)}\n\n{full_squad_marker}",
        1,
    )
    await app.send_message(chat_id, report, parse_mode="HTML")
