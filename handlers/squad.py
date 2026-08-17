from __future__ import annotations

print("squad.py loaded")

import html

from handlers.registry import register
from app import app
from engines.lineup_engine import load_squad, load_current_xi
from database.user_stats_repo import ensure_franchise_name
from utils.country_flags import flag_for


def _captain_id(squad: list[dict]) -> int | None:
    if not squad:
        return None
    player = max(squad, key=lambda p: (int(p.get("bat_level") or 0) + int(p.get("bowl_level") or 0), int(p.get("player_id") or 0)))
    return int(player.get("player_id") or 0)


def _icon(player: dict) -> str:
    role = str(player.get("role") or "").lower()
    if "all" in role:
        return "🔄"
    if "bowl" in role:
        return "⚡"
    return "🏏"


@register("squad")
async def squad_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    first_name = from_user.get("first_name")

    print(f"[squad] /squad invoked by user_id={user_id}")

    squad = await load_squad(user_id)
    if not squad:
        await app.send_message(chat_id, "⚠️ No squad found yet. Use /debut first to create your team.")
        return

    team_name = await ensure_franchise_name(user_id, first_name)
    captain_id = _captain_id(squad)
    lines = [
        "╭━━━〔 🏏 SQUAD 〕━━━╮",
        "",
        f"➤ <b>{html.escape(team_name)}</b>",
        f"➤ 👥 <b>Squad:</b> {len(squad)}/25",
        "",
        "<blockquote>",
    ]
    total = min(len(squad), 25)
    for i, player in enumerate(squad[:25], start=1):
        name = html.escape(str(player.get("name") or "Player"))
        level = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
        flag = flag_for(player.get("country"))
        icon = _icon(player)
        cap = " 🧢" if captain_id and int(player.get("player_id") or 0) == captain_id else ""
        prefix = "╰" if i == total else "├"
        lines.append(f"{prefix} {i}. {name} • {level} {flag} {icon}{cap}")
    lines += ["</blockquote>", ""]

    xi = await load_current_xi(user_id) or []
    xi_count = min(len(xi), 11)
    sub_count = max(0, len(squad) - xi_count)
    lines += [
        f"➤ 🏏 <b>Playing XI:</b> {xi_count}",
        f"➤ 🔁 <b>Substitutes:</b> {sub_count}",
        "",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ]

    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    print(f"[squad] Sent squad report for user_id={user_id} ({len(squad)} players)")
